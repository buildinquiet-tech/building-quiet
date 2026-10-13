#!/usr/bin/env python3
"""PreToolUse guard for Bash: refuse a short list of commands that cannot be undone.

Exit 2 blocks the call and sends the reason to Claude. Exit 0 lets it through.
Exit 1 does NOT block: Claude Code treats it as a non-blocking error.
"""
import json
import re
import shlex
import sys


def rm_recursive_force(tokens):
    """rm with both a recursive and a force flag, however they are written."""
    if "rm" not in tokens:
        return False
    flags = [t for t in tokens[tokens.index("rm") + 1:] if t.startswith("-")]
    short = "".join(f[1:] for f in flags if not f.startswith("--"))
    recursive = "r" in short.lower() or "--recursive" in flags
    force = "f" in short or "--force" in flags
    return recursive and force


BLOCKED = [
    (r"\bgit\s+push\b.*(--force\b|\s-f\b)", "git push --force"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
]

call = json.load(sys.stdin)
command = call.get("tool_input", {}).get("command", "")

try:
    tokens = shlex.split(command)
except ValueError:
    tokens = command.split()

if rm_recursive_force(tokens):
    print("Blocked by check-command.py: rm -rf is not allowed here.", file=sys.stderr)
    sys.exit(2)

for pattern, name in BLOCKED:
    if re.search(pattern, command):
        print(f"Blocked by check-command.py: {name} is not allowed here.", file=sys.stderr)
        sys.exit(2)

sys.exit(0)
