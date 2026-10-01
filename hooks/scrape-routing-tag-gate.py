#!/usr/bin/env python3
"""scrape-routing-tag-gate.py — PreToolUse hook (Edit/Write).

Refuses Write/Edit to echo/scrape/*.md or echo/research/*.md if the file's
frontmatter lacks `routing_tag: <CONSUME|ADAPT|STEAL|WATCH>`.

Closes Intel Activation Loop §Layer 1 (carryover #133a STUCK 15 sessions).
Every research/scrape output exits with an activation route.

Override: SENTINEL_OVERRIDE=scrape-routing-tag (one tool call only).
"""
import json
import os
import re
import sys

VALID_TAGS = {"CONSUME", "ADAPT", "STEAL", "WATCH"}
TAG_RE = re.compile(r"^---\s*\n.*?routing_tag:\s*([A-Z]+).*?\n---", re.DOTALL | re.IGNORECASE)
GUARDED_PATH_FRAGMENTS = ("/echo/scrape/", "/echo/research/")
# Only enforce routing-tag on markdown research outputs.
# Config/state files (.yml, .yaml, .json, .jsonl, .gitkeep) are exempt —
# they aren't research artifacts, just engine plumbing.
GUARDED_EXTENSIONS = (".md",)


def is_guarded_path(file_path: str) -> bool:
    if not any(frag in file_path for frag in GUARDED_PATH_FRAGMENTS):
        return False
    return file_path.endswith(GUARDED_EXTENSIONS)


def extract_routing_tag(content: str):
    m = TAG_RE.search(content or "")
    return m.group(1).upper() if m else None


def deny(reason: str):
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))
    sys.exit(0)


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "scrape-routing-tag":
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "NotebookEdit"):
        sys.exit(0)

    tin = data.get("tool_input", {})
    fp = str(tin.get("file_path", ""))
    if not is_guarded_path(fp):
        sys.exit(0)

    # For Edit, trust existing file already has tag (backfill assumption).
    # Only Write is checked at v1.
    if tool_name == "Edit":
        sys.exit(0)

    content = str(tin.get("content") or "")
    tag = extract_routing_tag(content)
    if tag is None:
        deny(
            "[SCRAPE ROUTING TAG] Missing `routing_tag:` in frontmatter.\n\n"
            "Per Intel Activation Loop v0.1 Layer 1: every echo/scrape/ and "
            "echo/research/ output MUST exit with one of:\n"
            "  - CONSUME — learn-only, no downstream\n"
            "  - ADAPT   — frame extraction for our content (feeds drafts/intel-adapt-queue.md)\n"
            "  - STEAL   — clone the workflow/tool/skill (feeds /tool-intel pipeline)\n"
            "  - WATCH   — escalate priority on next drop from this source\n\n"
            "Add to YAML frontmatter:\n"
            "  routing_tag: <CONSUME|ADAPT|STEAL|WATCH>\n\n"
            "Override (one call): SENTINEL_OVERRIDE=scrape-routing-tag"
        )

    if tag not in VALID_TAGS:
        deny(
            f"[SCRAPE ROUTING TAG] Invalid routing_tag value: '{tag}'.\n\n"
            f"Must be one of: {sorted(VALID_TAGS)}.\n\n"
            "Override (one call): SENTINEL_OVERRIDE=scrape-routing-tag"
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
