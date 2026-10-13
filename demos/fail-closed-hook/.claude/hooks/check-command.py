#!/usr/bin/env python3
"""PreToolUse guard for Bash: refuse a short list of commands that cannot be undone.

Exit 2 blocks the call and sends the reason to Claude. Exit 0 lets it through.
Exit 1 does NOT block: Claude Code treats it as a non-blocking error.
"""
import json
import re
import sys

BLOCKED = [
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "rm -rf"),
    (r"\bgit\s+push\b.*(--force\b|-f\b)", "git push --force"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
]

call = json.load(sys.stdin)
command = call.get("tool_input", {}).get("command", "")

for pattern, name in BLOCKED:
    if re.search(pattern, command):
        print(f"Blocked by check-command.py: {name} is not allowed here.", file=sys.stderr)
        sys.exit(2)

sys.exit(0)
