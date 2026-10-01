#!/usr/bin/env python3
"""
Memory Sync Hook — PostToolUse on Edit|Write
Auto-copies repo memory files to auto-memory after every edit.
Eliminates split-brain drift between repo and ~/.claude/projects/.
Created: 2026-04-02 (S154)
"""

import json
import os
import shutil
import sys


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        return

    # Only trigger for memory/*.md files in this project
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return

    memory_dir = os.path.join(project_dir, "memory")
    if not file_path.startswith(memory_dir) or not file_path.endswith(".md"):
        return

    # Derive auto-memory path
    auto_memory_dir = os.path.join(
        os.path.expanduser("~"),
        ".claude", "projects",
        project_dir.replace("/", "-"),
        "memory"
    )

    if not os.path.isdir(auto_memory_dir):
        return

    # Copy the edited file to auto-memory
    basename = os.path.basename(file_path)
    dest = os.path.join(auto_memory_dir, basename)

    try:
        shutil.copy2(file_path, dest)
    except Exception:
        pass  # Silent failure — don't block Echo's work


if __name__ == "__main__":
    main()
