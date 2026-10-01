#!/usr/bin/env python3
"""PostToolUse hook: enforces transcript creation at session start.

After 5 tool calls, checks if a transcript file exists for today.
If not, prints WARNING every 10 tool calls until one is created.
"""

import json
import os
import sys
from datetime import datetime
from glob import glob

STATE_FILE = "/tmp/echo-transcript-check.json"
PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TRANSCRIPTS_DIR = os.path.join(PROJECT_DIR, "echo", "transcripts")

FIRST_CHECK = 5
REMIND_INTERVAL = 10


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        # Reset if stale (>2 hours)
        last = datetime.fromisoformat(state.get("last_updated", "2000-01-01"))
        if (datetime.now() - last).total_seconds() > 7200:
            return {"call_count": 0, "warned": False, "last_updated": ""}
        return state
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return {"call_count": 0, "warned": False, "last_updated": ""}


def save_state(state):
    state["last_updated"] = datetime.now().isoformat(timespec="seconds")
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def transcript_exists():
    """Check if a transcript file exists for today."""
    today = datetime.now().strftime("%Y-%m-%d")
    pattern = os.path.join(TRANSCRIPTS_DIR, f"{today}*.md")
    return len(glob(pattern)) > 0


def main():
    try:
        sys.stdin.read()
    except Exception:
        pass

    state = load_state()
    state["call_count"] = state.get("call_count", 0) + 1
    count = state["call_count"]

    # Don't check until we've had enough tool calls
    if count < FIRST_CHECK:
        save_state(state)
        return

    # If transcript exists, stay quiet
    if transcript_exists():
        state["warned"] = False
        save_state(state)
        return

    # Fire warning at first check and then every REMIND_INTERVAL calls
    if count == FIRST_CHECK or (count - FIRST_CHECK) % REMIND_INTERVAL == 0:
        print(f">>> TRANSCRIPT MISSING ({count} tool calls, no transcript for today)")
        print("    Decisions are NOT being recorded. Create echo/transcripts/YYYY-MM-DD-S###.md NOW.")
        print("    This is how S131 lost data. Don't repeat it.")
        state["warned"] = True

    save_state(state)


if __name__ == "__main__":
    main()
