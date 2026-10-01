#!/usr/bin/env python3
"""PostToolUse hook: nudges Echo to commit after N write operations.

Tracks Edit/Write operations to files outside .claude/.
After 10 writes, prints a reminder to commit produced assets.
Resets when a git commit is detected in Bash output.
"""

import json
import os
import sys
from datetime import datetime

STATE_FILE = "/tmp/echo-commit-nudge.json"
NUDGE_THRESHOLD = 10
STALE_MINUTES = 30


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        last = datetime.fromisoformat(state.get("last_updated", "2000-01-01"))
        if (datetime.now() - last).total_seconds() > STALE_MINUTES * 60:
            return {"write_count": 0, "files_modified": [], "last_updated": ""}
        return state
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return {"write_count": 0, "files_modified": [], "last_updated": ""}


def save_state(state):
    state["last_updated"] = datetime.now().isoformat(timespec="seconds")
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, Exception):
        input_data = {}

    state = load_state()
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    tool_output = input_data.get("tool_output", "")

    # Reset on git commit detection
    if tool_name == "Bash":
        output_str = str(tool_output)
        if "git commit" in str(tool_input.get("command", "")) or \
           "] " in output_str and ("file changed" in output_str or "files changed" in output_str):
            state["write_count"] = 0
            state["files_modified"] = []
            save_state(state)
            return

    # Track writes (Edit/Write only, skip .claude/ internal files)
    if tool_name in ("Edit", "Write"):
        file_path = tool_input.get("file_path", "")
        if "/.claude/" not in file_path and file_path:
            state["write_count"] = state.get("write_count", 0) + 1
            modified = state.get("files_modified", [])
            basename = os.path.basename(file_path)
            if basename not in modified:
                modified.append(basename)
                # Keep list manageable
                if len(modified) > 20:
                    modified = modified[-20:]
            state["files_modified"] = modified

    count = state.get("write_count", 0)

    # Nudge at threshold and every threshold after
    if count > 0 and count % NUDGE_THRESHOLD == 0:
        files = state.get("files_modified", [])
        file_preview = ", ".join(files[:5])
        if len(files) > 5:
            file_preview += f" +{len(files) - 5} more"
        print(f">>> COMMIT NUDGE: {count} writes since last commit ({file_preview})")
        print("    Uncommitted work = unrecoverable work. Commit produced assets now.")

    save_state(state)


if __name__ == "__main__":
    main()
