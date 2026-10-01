#!/usr/bin/env python3
"""
Plan Archiver Hook — PreToolUse on Write
Automatically archives plan files before they're overwritten.
Created: 2026-03-20 (S95)
"""

import json
import os
import sys
import shutil
from datetime import datetime

def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        # Can't parse input — allow the write
        print(json.dumps({"decision": "allow"}))
        return

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Only intercept Write tool
    if tool_name != "Write":
        print(json.dumps({"decision": "allow"}))
        return

    file_path = tool_input.get("file_path", "")

    # Only intercept plan files
    if "/.claude/plans/" not in file_path or not file_path.endswith(".md"):
        print(json.dumps({"decision": "allow"}))
        return

    # Check if file exists and has substantial content (>50 lines = not a stub)
    if not os.path.exists(file_path):
        print(json.dumps({"decision": "allow"}))
        return

    try:
        with open(file_path, "r") as f:
            content = f.read()
            line_count = content.count("\n") + 1
    except Exception:
        print(json.dumps({"decision": "allow"}))
        return

    if line_count <= 50:
        print(json.dumps({"decision": "allow"}))
        return

    # Archive the existing file before it gets overwritten
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        # Try to derive from the file path
        plans_idx = file_path.find("/.claude/plans/")
        if plans_idx > 0:
            project_dir = file_path[:plans_idx]
        else:
            print(json.dumps({"decision": "allow"}))
            return

    archive_dir = os.path.join(project_dir, "docs", "archive", "plans")
    os.makedirs(archive_dir, exist_ok=True)

    # Generate archive filename: YYYY-MM-DD-{original-name}.md
    original_name = os.path.basename(file_path).replace(".md", "")
    today = datetime.now().strftime("%Y-%m-%d")
    archive_name = f"{today}-{original_name}.md"
    archive_path = os.path.join(archive_dir, archive_name)

    # Handle duplicates: append -v2, -v3, etc.
    if os.path.exists(archive_path):
        version = 2
        while os.path.exists(os.path.join(archive_dir, f"{today}-{original_name}-v{version}.md")):
            version += 1
        archive_name = f"{today}-{original_name}-v{version}.md"
        archive_path = os.path.join(archive_dir, archive_name)

    try:
        shutil.copy2(file_path, archive_path)
        # Allow the write but print a warning
        print(json.dumps({
            "decision": "allow",
            "reason": f"ARCHIVED: Plan file '{original_name}.md' ({line_count} lines) backed up to docs/archive/plans/{archive_name} before overwrite."
        }))
    except Exception as e:
        # Archive failed — still allow the write, but warn
        print(json.dumps({
            "decision": "allow",
            "reason": f"WARNING: Failed to archive plan file before overwrite: {e}"
        }))

if __name__ == "__main__":
    main()
