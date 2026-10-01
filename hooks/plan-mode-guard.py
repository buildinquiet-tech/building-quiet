#!/usr/bin/env python3
"""
Plan-Mode Write Guard — Blocks non-plan-file writes when plan mode is active.

Runs as a PreToolUse hook before Edit|Write operations.
If .claude/hooks/.plan-mode-active exists, ONLY plan file edits are allowed.

Flag file lifecycle:
  - Created by Echo when entering plan mode (or by EnterPlanMode signal)
  - Deleted by Echo when exiting plan mode (or by ExitPlanMode signal)
  - Hook checks for existence — deterministic, no rationalization possible

S190: 4th instance of execution-bypassing-planning (S140/S164/S182/S190).
This hook exists because model-level instructions alone don't prevent violations.
"""

import json
import os
import sys

PROJECT_ROOT = os.environ.get(
    "CLAUDE_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

FLAG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".plan-mode-active")

# Plan files are the ONLY thing allowed during plan mode
# Plans can be in project .claude/plans/ OR user ~/.claude/plans/
PLAN_DIRS = [
    os.path.join(PROJECT_ROOT, ".claude", "plans"),
    os.path.join(os.path.expanduser("~"), ".claude", "plans"),
]


def resolve_relative_path(file_path):
    """Resolve file_path to a relative path from PROJECT_ROOT."""
    file_path = os.path.normpath(file_path)
    project_root = os.path.normpath(PROJECT_ROOT)
    if os.path.isabs(file_path):
        if file_path.startswith(project_root + os.sep):
            return file_path[len(project_root) + 1:]
        elif file_path == project_root:
            return ""
        else:
            return None
    return file_path


def is_plan_file(file_path):
    """Check if the target file is a plan file (allowed during plan mode)."""
    norm = os.path.normpath(file_path)
    for plan_dir in PLAN_DIRS:
        plan_dir_norm = os.path.normpath(plan_dir)
        if norm.startswith(plan_dir_norm + os.sep) or norm == plan_dir_norm:
            return True
    return False


def main():
    # If plan mode is NOT active, pass through silently
    if not os.path.exists(FLAG_FILE):
        sys.exit(0)

    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        sys.exit(0)

    # Allow plan file edits
    if is_plan_file(file_path):
        sys.exit(0)

    # Block everything else
    rel_path = resolve_relative_path(file_path)
    display_path = rel_path if rel_path else file_path

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PLAN MODE ACTIVE: Cannot write to {display_path}. "
                "Only plan file edits are allowed during plan mode. "
                "Exit plan mode before modifying other files. "
                "To deactivate: delete .claude/hooks/.plan-mode-active"
            ),
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
