#!/usr/bin/env python3
"""skill-pre-read-sentinel.py — PreToolUse hook (generalized pbl pattern).

Blocks first state-changing tool call within a skill's territory until
that skill's required-reads manifest has been satisfied in the current
session's transcript.

Generalizes pbl-pre-read-sentinel.py from PBL-only to per-skill manifests.

Triggers per skill (see SKILL_MANIFESTS):
  - Edit/Write to a manifest-declared path
  - Bash command matching a manifest-declared pattern

Required reads:
  - Filename (basename) appears in current-day Echo transcript OR recent
    Claude Code JSONL transcript (last 24h).

Override: SENTINEL_OVERRIDE=skill-pre-read (one call only).
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
TRANSCRIPT_DIR = os.path.join(REPO_DIR, "echo", "transcripts")
FIRE_LOG = os.path.join(HOOKS_DIR, "skill-pre-read-sentinel-fire-log.jsonl")

_project_hash = REPO_DIR.replace("/", "-")
JSONL_DIR = os.path.expanduser(f"~/.claude/projects/{_project_hash}")
JSONL_RECENCY_HOURS = 24


def log_fire(decision, reason="", **extra):
    """Append one JSONL record per meaningful invocation. Never raises."""
    rec = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hook": "skill-pre-read-sentinel",
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


SKILL_MANIFESTS = {
    "schedule": {
        "triggers_paths": ["/drafts/scheduled/", "/echo/state/schedule-"],
        "triggers_bash_patterns": [r"metricool\.com.*scheduler/posts.*-X\s+POST"],
        "required_reads": [
            "publishing-reference.md",
            "cmo-brief.md",
            "publishing-rules.md",
        ],
    },
    "post": {
        "triggers_paths": ["/drafts/brand-qa/"],
        "triggers_bash_patterns": [],
        "required_reads": [
            "cmo-brief.md",
            "content-rulebook.md",
            "brand-config.md",
            "content-log.md",
        ],
    },
    "closing": {
        "triggers_paths": ["/echo/handoffs/"],
        "triggers_bash_patterns": [],
        "required_reads": [
            "active-work.md",
            "queue-status.md",
        ],
    },
    "save-state": {
        "triggers_paths": [],
        "triggers_bash_patterns": [],
        "required_reads": ["active-work.md"],
    },
    "scrape": {
        "triggers_paths": ["/echo/scrape/", "/echo/dispatches/"],
        "triggers_bash_patterns": [],
        "required_reads": ["scrape-queue.md"],
    },
}


def get_current_transcript():
    if not os.path.isdir(TRANSCRIPT_DIR):
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    p = os.path.join(TRANSCRIPT_DIR, f"{today}.md")
    if os.path.exists(p):
        return p
    files = sorted(glob.glob(os.path.join(TRANSCRIPT_DIR, "*.md")), reverse=True)
    return files[0] if files else None


def transcript_mentions(needle: str) -> bool:
    nlow = needle.lower()
    p = get_current_transcript()
    if p:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                if nlow in f.read().lower():
                    return True
        except (OSError, UnicodeDecodeError):
            pass
    if os.path.isdir(JSONL_DIR):
        cutoff = datetime.now().timestamp() - JSONL_RECENCY_HOURS * 3600
        try:
            for fn in os.listdir(JSONL_DIR):
                if not fn.endswith(".jsonl"):
                    continue
                fp = os.path.join(JSONL_DIR, fn)
                try:
                    if os.path.getmtime(fp) < cutoff:
                        continue
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        if nlow in f.read().lower():
                            return True
                except (OSError, UnicodeDecodeError):
                    continue
        except OSError:
            pass
    return False


def detect_skill(tool_name: str, tool_input: dict):
    """Return (skill_name, manifest) or (None, None)."""
    if not isinstance(tool_input, dict):
        return None, None

    if tool_name in ("Edit", "Write", "NotebookEdit"):
        path = (tool_input.get("file_path") or "").lower()
        for skill, m in SKILL_MANIFESTS.items():
            for trig in m["triggers_paths"]:
                if trig.lower() in path:
                    return skill, m
        return None, None

    if tool_name == "Bash":
        cmd = tool_input.get("command") or ""
        for skill, m in SKILL_MANIFESTS.items():
            for pat in m["triggers_bash_patterns"]:
                if re.search(pat, cmd, re.IGNORECASE):
                    return skill, m
        return None, None

    return None, None


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "skill-pre-read":
        log_fire("pass", "override_env")
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    skill, manifest = detect_skill(tool_name, tool_input)
    if skill is None:
        sys.exit(0)

    missing_names = [
        fname for fname in manifest["required_reads"]
        if not transcript_mentions(fname)
    ]
    if not missing_names:
        log_fire("pass", "all_reads_present", skill=skill, tool=tool_name)
        sys.exit(0)

    missing = [f"  - {fname}" for fname in missing_names]
    msg = (
        f"[SKILL PRE-READ HALT] /{skill} territory entered without reading "
        f"required manifest files this session.\n"
        f"Missing pre-reads:\n" + "\n".join(missing) + "\n\n"
        f"Required: Read each file via the Read tool BEFORE the first "
        f"state-changing /{skill} action. Then re-attempt this tool call.\n\n"
        f"Override (one call): SENTINEL_OVERRIDE=skill-pre-read"
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        }
    }
    log_fire("block", "missing_reads", skill=skill, tool=tool_name, missing=missing_names)
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
