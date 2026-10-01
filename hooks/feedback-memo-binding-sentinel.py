#!/usr/bin/env python3
"""feedback-memo-binding-sentinel.py — Stop hook.

Blocks turn-end if a hard-class feedback memo was Written this session
without a corresponding hook script also being Written/Edited.

Closes the "36th memo problem": new rules tagged enforcement=hard must
either gain a hook same session, or be demoted to soft, or be rejected
as "too vague to enforce."

Override: SENTINEL_OVERRIDE=memo-binding (one turn only)
"""
import json
import os
import re
import sys
from datetime import datetime


HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
FIRE_LOG = os.path.join(HOOKS_DIR, "feedback-memo-binding-sentinel-fire-log.jsonl")

HARD_FRONTMATTER_RE = re.compile(
    r"^---\s*\n.*?enforcement:\s*hard.*?\n---", re.DOTALL | re.IGNORECASE
)


def log_fire(decision, reason="", **extra):
    """Append one JSONL record per meaningful invocation. Never raises."""
    rec = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hook": "feedback-memo-binding-sentinel",
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


def iter_tool_uses(transcript_path):
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


def is_feedback_memo_path(file_path: str) -> bool:
    name = os.path.basename(file_path or "")
    return name.startswith("feedback_") and name.endswith(".md")


def is_currently_hard(file_path: str) -> bool:
    """Read the CURRENT file state and check if frontmatter is hard.

    Re-reading current state (vs. event content) is what makes demote-based
    resolution work — an Edit that flips enforcement: hard -> soft must clear
    the gate, not stay flagged because the original Write was hard.
    """
    if not file_path or not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return bool(HARD_FRONTMATTER_RE.search(f.read()))
    except (OSError, UnicodeDecodeError):
        return False


def is_hook_write(file_path: str) -> bool:
    return ".claude/hooks/" in (file_path or "")


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "memo-binding":
        log_fire("pass", "override_env")
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)

    transcript_path = data.get("transcript_path", "")

    touched_memos = set()
    hook_writes = []
    for name, tin in iter_tool_uses(transcript_path):
        if name not in ("Write", "Edit"):
            continue
        fp = str(tin.get("file_path", ""))
        if is_feedback_memo_path(fp):
            touched_memos.add(fp)
        if is_hook_write(fp):
            hook_writes.append(fp)

    hard_memo_paths = [p for p in sorted(touched_memos) if is_currently_hard(p)]

    if not hard_memo_paths:
        if touched_memos:
            log_fire("pass", "memos_touched_none_hard", memo_count=len(touched_memos))
        sys.exit(0)
    if hook_writes:
        log_fire(
            "pass", "memo_with_paired_hook",
            memos=[os.path.basename(p) for p in hard_memo_paths],
            hook_writes=[os.path.basename(p) for p in hook_writes][:5],
        )
        sys.exit(0)

    msg = (
        "[MEMO-BINDING GATE] Hard-class feedback memo created this session "
        "without a corresponding hook script.\n\n"
        f"Hard memos written:\n" + "\n".join(f"  - {p}" for p in hard_memo_paths) + "\n\n"
        "Per `feedback_rules_without_enforcement.md` (S307): a feedback memo "
        "tagged `enforcement: hard` is decoration unless backed by a hook, "
        "sentinel, or skill-step. The 36th-memo problem is exactly this — "
        "rules accumulate as text, none of them fire when needed.\n\n"
        "Required: One of the following before turn-end:\n"
        "  1. Write a hook under .claude/hooks/ that enforces the rule, OR\n"
        "  2. Demote the memo's frontmatter `enforcement:` field to `soft` "
        "or `judgment` (and update memory/rule-binding-ledger.md), OR\n"
        "  3. Delete the memo (the rule was too vague to enforce).\n\n"
        "Override (this turn only): SENTINEL_OVERRIDE=memo-binding"
    )
    log_fire(
        "block", "memo_without_paired_hook",
        memos=[os.path.basename(p) for p in hard_memo_paths],
    )
    print(json.dumps({"decision": "block", "reason": msg}))
    sys.exit(0)


if __name__ == "__main__":
    main()
