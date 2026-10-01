#!/usr/bin/env python3
"""Schedule Queue Sync Sentinel — Stop hook.

Enforces /schedule SKILL.md Exit Gate: if scheduling activity touched
content-log.md or batch-log.md this session, queue-status.md must also
be edited before the turn ends. Prevents the S287 drift class where
Metricool posts shipped, content-log got the diff, but queue-status.md
went 3+ days stale and lied to the next session's dashboards.

Trigger: Stop hook fires after assistant emits a stop signal.
Behavior:
  - Read transcript_path from stdin JSON
  - Walk THIS session's tool_use events
  - Detect scheduling write activity:
      A) Edit/Write to content-log.md or batch-log.md, OR
      B) Bash command hitting Metricool scheduler endpoint
  - If detected: require Edit/Write to queue-status.md in same session
  - If queue-status.md NOT touched → block with reason

Override: SENTINEL_OVERRIDE=queue-sync (skip this turn only)
"""

import json
import sys
import os
import re

# Files that indicate scheduling write activity occurred
SCHEDULE_WRITE_TARGETS = (
    "content-log.md",
    "batch-log.md",
)

# File the gate requires updated alongside any scheduling write
QUEUE_STATUS_FILE = "queue-status.md"

# Top-level real-call detection. Inverts prior approach (negative-skip of
# noisy shapes) to positive identification. Adversarial test harness
# (scripts/test_schedule_sentinel_adversarial.py) exposed that the
# negative-skip enumeration was incomplete (git commit messages, echo
# without pipe, python -c print, var assignments, etc. all leaked
# through). Positive detection on TOP-LEVEL curl/python -c is robust:
# anything else is skipped regardless of body content.
ENV_PREFIX_RE = re.compile(r"^[A-Z_][A-Z0-9_]*=\S+\s+")


def strip_env_prefix(stripped: str) -> str:
    while ENV_PREFIX_RE.match(stripped):
        stripped = ENV_PREFIX_RE.sub("", stripped, count=1)
    return stripped


def is_top_level_curl_state_change(cmd: str) -> bool:
    """True iff the command's top-level invocation is curl (or wget) firing
    a state-changing call (POST/PUT/DELETE) against scheduler/posts.
    queue-sync fires on all three; state-gate is POST-only and uses a
    different helper.
    """
    stripped = strip_env_prefix(cmd.lstrip())
    if not stripped.startswith(("curl ", "/usr/bin/curl ", "wget ")):
        return False
    lower = stripped.lower()
    if "metricool.com" not in lower:
        return False
    if "/scheduler/posts" not in lower:
        return False
    if "-x get" in lower:
        return False
    return ("-x post" in lower or "-x put" in lower or "-x delete" in lower
            or " -d " in lower or " --data" in lower)


def is_top_level_python_state_change(cmd: str) -> bool:
    """True iff the command is `python3 -c "..."` (one-liner, NOT heredoc)
    that calls requests.{post,put,delete}() against scheduler/posts.
    """
    stripped = strip_env_prefix(cmd.lstrip())
    if not re.match(r"^python3?\s+-c\s+", stripped):
        return False
    lower = stripped.lower()
    if "metricool.com" not in lower:
        return False
    if "/scheduler/posts" not in lower:
        return False
    return bool(re.search(r"requests\.(post|put|delete)\s*\(", stripped, re.IGNORECASE))


def is_metricool_write(cmd: str) -> bool:
    return is_top_level_curl_state_change(cmd) or is_top_level_python_state_change(cmd)


def iter_tool_uses(transcript_path):
    """Yield (tool_name, tool_input_dict) for every tool_use in the transcript."""
    if not transcript_path or not os.path.exists(transcript_path):
        return
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") != "assistant":
                    continue
                msg = evt.get("message", {})
                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        yield blk.get("name", ""), blk.get("input", {}) or {}
    except (OSError, UnicodeDecodeError):
        return


def detect_scheduling_activity(transcript_path):
    """Return (touched_log, touched_queue_status, evidence_path) for this session."""
    touched_log = False
    touched_queue_status = False
    evidence_path = None

    for name, tin in iter_tool_uses(transcript_path):
        # File edits / writes
        if name in ("Edit", "Write", "NotebookEdit"):
            fp = str(tin.get("file_path", ""))
            if any(tgt in fp for tgt in SCHEDULE_WRITE_TARGETS):
                touched_log = True
                evidence_path = fp
            if QUEUE_STATUS_FILE in fp:
                touched_queue_status = True

        # Bash commands hitting Metricool scheduler write endpoints —
        # only top-level curl / python -c invocations count as real calls.
        elif name == "Bash":
            cmd = str(tin.get("command", ""))
            if is_metricool_write(cmd):
                touched_log = True
                evidence_path = "Metricool scheduler write"

    return touched_log, touched_queue_status, evidence_path


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "queue-sync":
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)

    transcript_path = data.get("transcript_path", "")
    touched_log, touched_queue_status, evidence = detect_scheduling_activity(transcript_path)

    if not touched_log:
        sys.exit(0)

    if touched_queue_status:
        sys.exit(0)

    msg = (
        f"[QUEUE SYNC GATE] Scheduling activity detected this session "
        f"({evidence}) but `docs/_active/queue-status.md` was NOT edited.\n\n"
        f"Per /schedule SKILL.md Exit Gate: every batch must update "
        f"queue-status.md (scheduled counts, date ranges, post IDs) so the "
        f"next session's /status and /echo dashboards stay truthful. "
        f"Drift here is the S287 class — content-log got the diff, "
        f"queue-status went stale, dashboards lied for 3 days.\n\n"
        f"PREFERRED: Pre-tag pattern (log as work moves through flow):\n"
        f"  1. BEFORE Metricool call → add row to queue-status.md tagged "
        f"`PENDING` with intended date/time/content ID\n"
        f"  2. Run the Metricool POST/PUT\n"
        f"  3. AFTER success → edit row to swap PENDING → confirmed post IDs\n"
        f"  Why: crash-safe (intent recorded even if API hangs), matches "
        f"LAW 5 Two-Phase shape, future sessions see intent before receipts.\n\n"
        f"FALLBACK: Post-log pattern (faster, loses intent on crash):\n"
        f"  1. Edit queue-status.md with new ships (date, content ID, time, "
        f"Metricool post IDs)\n"
        f"  2. Bump `Last updated:` line to today's session\n"
        f"  3. Move stale 'deferred' rows to SUPERSEDED if rendered\n\n"
        f"Override (this turn only): SENTINEL_OVERRIDE=queue-sync"
    )

    output = {
        "decision": "block",
        "reason": msg,
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
