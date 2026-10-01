#!/usr/bin/env python3
"""schedule-state-gate.py — PreToolUse Bash hook.

Refuses Metricool POST /scheduler/posts unless:
  1. A state file matching today's pattern exists in echo/state/
  2. The state file contains a post-record at status='validated'
  3. The record's media_hosted_url matches a URL in the POST payload

Closes the gap between metricool-post-verify-pre.py (payload-shape gate)
and the actual procedural-position requirement: did this post pass through
compose -> media_uploaded -> validated before we hit the API?

Exit 0 = allow. Exit 2 + stderr = block (Claude Code halts the tool call).

Override: SENTINEL_OVERRIDE=schedule-state (one call only)
"""
import json
import os
import re
import sys
import glob
from datetime import datetime


HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HOOKS_DIR)
REPO_DIR = os.path.dirname(PROJECT_DIR)
STATE_DIR = os.path.join(REPO_DIR, "echo", "state")
FIRE_LOG = os.path.join(HOOKS_DIR, "schedule-state-gate-fire-log.jsonl")

REQUIRED_STATUS = "validated"

URL_RE = re.compile(r"https?://[^\s\"'\\]+", re.IGNORECASE)
ENV_PREFIX_RE = re.compile(r"^[A-Z_][A-Z0-9_]*=\S+\s+")


def log_fire(decision, reason="", **extra):
    """Append one JSONL record per meaningful invocation. Never raises."""
    rec = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hook": "schedule-state-gate",
        "decision": decision,
        "reason": reason,
    }
    if extra:
        rec.update(extra)
    try:
        with open(FIRE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def strip_env_prefix(stripped: str) -> str:
    """Strip leading env-var assignments: `TOKEN=foo VAR=bar curl ...` -> `curl ...`."""
    while ENV_PREFIX_RE.match(stripped):
        stripped = ENV_PREFIX_RE.sub("", stripped, count=1)
    return stripped


def is_top_level_curl_post(cmd: str) -> bool:
    """True iff the command's TOP-LEVEL invocation is curl (or wget) firing
    a metricool scheduler POST. Direct curl is the ONLY shape we positively
    identify as real — every other shape (heredoc, echo, git commit, var
    assignment, grep) is treated as noise.
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
    if "-x delete" in lower or "-x put" in lower:
        # state-gate is POST-only; PUT/DELETE bypass this gate
        return False
    return ("-x post" in lower or " -d " in lower or " --data" in lower)


def is_top_level_python_post(cmd: str) -> bool:
    """True iff the command is `python3 -c "..."` (one-liner, NOT heredoc)
    that calls requests.post() against scheduler/posts. Heredocs are too
    noisy to parse and treated as skip.
    """
    stripped = strip_env_prefix(cmd.lstrip())
    if not re.match(r"^python3?\s+-c\s+", stripped):
        return False
    lower = stripped.lower()
    if "metricool.com" not in lower:
        return False
    if "/scheduler/posts" not in lower:
        return False
    return bool(re.search(r"requests\.post\s*\(", stripped, re.IGNORECASE))


def looks_like_metricool_post(cmd: str) -> bool:
    return is_top_level_curl_post(cmd) or is_top_level_python_post(cmd)


def extract_payload_urls(cmd: str):
    urls = []
    for flag in (r"-d\s+'([^']+)'", r'-d\s+"([^"]+)"',
                 r"--data\s+'([^']+)'", r'--data\s+"([^"]+)"'):
        m = re.search(flag, cmd)
        if m:
            urls.extend(URL_RE.findall(m.group(1)))
            return [u for u in urls if "metricool.com" not in u.lower()]
    urls.extend(URL_RE.findall(cmd))
    return [u for u in urls if "metricool.com" not in u.lower()]


def find_today_state_files():
    if not os.path.isdir(STATE_DIR):
        return []
    today = datetime.now().strftime("%Y%m%d")
    pattern = os.path.join(STATE_DIR, f"schedule-{today}-*.json")
    return sorted(glob.glob(pattern))


def find_matching_record(state_files, payload_urls):
    """Return (matched_record, summary_lines)."""
    summary = []
    payload_url_set = {u.strip().lower() for u in payload_urls}
    for sf in state_files:
        try:
            with open(sf, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for rec in state.get("posts", []):
            url = (rec.get("media_hosted_url") or "").strip().lower()
            status = rec.get("status", "unknown")
            summary.append(
                f"  {rec.get('post_id', '?')}: status={status} url={url[:60] or 'none'}"
            )
            if not url:
                continue
            if url in payload_url_set:
                return rec, summary
    return None, summary


def block(reason: str, log_reason: str = "", **log_extra):
    log_fire("block", log_reason or "blocked", **log_extra)
    print(reason, file=sys.stderr)
    sys.exit(2)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "schedule-state":
        log_fire("pass", "override_env")
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    cmd = data.get("tool_input", {}).get("command", "") or ""
    if not cmd or not looks_like_metricool_post(cmd):
        sys.exit(0)

    state_files = find_today_state_files()
    if not state_files:
        block(
            "[SCHEDULE STATE GATE] BLOCKED — Metricool POST attempted but no "
            "state file exists at echo/state/schedule-{today}-*.json.\n\n"
            "Per /schedule SKILL.md §State Machine: every batch must write a "
            "state file as Phase 0 before any composition or upload. The state "
            "file is the procedural ledger that proves compose -> media_uploaded "
            "-> validated happened in order.\n\n"
            "Required:\n"
            "  1. Create echo/state/schedule-YYYYMMDD-NN.json (see SCHEMA.md)\n"
            "  2. Add a record per post with status='pending'\n"
            "  3. Advance status as you progress (compose, upload, validate)\n"
            "  4. Re-fire the Metricool POST after the matching record is "
            "status='validated'\n\n"
            "Override (one call): SENTINEL_OVERRIDE=schedule-state",
            log_reason="no_state_file",
        )

    payload_urls = extract_payload_urls(cmd)
    if not payload_urls:
        # Empty media is metricool-post-verify-pre.py's concern; defer.
        log_fire("skip", "empty_payload_urls_defer")
        sys.exit(0)

    rec, summary = find_matching_record(state_files, payload_urls)
    summary_text = "\n".join(summary[:10]) if summary else "  (no posts in state file)"
    if rec is None:
        block(
            "[SCHEDULE STATE GATE] BLOCKED — Metricool POST media URL does not "
            "match any post-record in today's state file.\n\n"
            f"Payload URLs: {payload_urls[:3]}\n"
            f"State-file records:\n{summary_text}\n\n"
            "Either the state file is missing this post, or the media URL "
            "drifted between Phase 2 (litterbox upload) and the POST. Add the "
            "record, OR update the existing record's media_hosted_url, OR "
            "override.\n\n"
            "Override (one call): SENTINEL_OVERRIDE=schedule-state",
            log_reason="no_matching_record",
            payload_urls=payload_urls[:3],
        )

    if rec.get("status") != REQUIRED_STATUS:
        block(
            f"[SCHEDULE STATE GATE] BLOCKED — Post {rec.get('post_id', '?')} "
            f"matched in state file but status is '{rec.get('status')}', "
            f"required '{REQUIRED_STATUS}'.\n\n"
            "Procedural sequence: pending -> composed -> media_uploaded -> "
            "validated -> scheduled -> verified. Cannot fire Metricool POST "
            "until status='validated' (validate-content.py + media-attach "
            "3-check passed).\n\n"
            f"State summary:\n{summary_text}\n\n"
            "Run validation step, update state file to status='validated', "
            "re-fire.\n\n"
            "Override (one call): SENTINEL_OVERRIDE=schedule-state",
            log_reason="status_not_validated",
            post_id=rec.get("post_id", "?"),
            status=rec.get("status"),
        )

    log_fire("pass", "clean", post_id=rec.get("post_id", "?"))
    sys.exit(0)


if __name__ == "__main__":
    main()
