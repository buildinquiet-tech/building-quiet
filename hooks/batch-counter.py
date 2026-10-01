#!/usr/bin/env python3
"""PostToolUse hook for Bash|Agent: enforces sliding-scale batch limits from the Execution Governor.

Sliding scale (based on workload type):
- Default: 25 write-ops (standard tasks)
- Research/catalog: 50 write-ops (data gathering, read-heavy)
- System maintenance: 40 write-ops (tidy, closing, diagnostics)

Override via /tmp/echo-batch-limit.json: {"limit": N, "reason": "..."}
"""

import json
import os
import sys
from datetime import datetime

STATE_FILE = "/tmp/echo-batch-counter.json"
LIMIT_OVERRIDE_FILE = "/tmp/echo-batch-limit.json"
STALE_MINUTES = 15

# Default limits
DEFAULT_LIMIT = 25
WARN_RATIO = 0.8  # Warn at 80% of limit


def load_state():
    """Load state file. Reset if missing or stale (>5 min)."""
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)

        last_updated = datetime.fromisoformat(state["last_updated"])
        elapsed = (datetime.now() - last_updated).total_seconds()

        if elapsed > STALE_MINUTES * 60:
            return {"count": 0, "last_updated": "", "warnings_issued": []}

        return state
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return {"count": 0, "last_updated": "", "warnings_issued": []}


def save_state(state):
    """Save state to file."""
    state["last_updated"] = datetime.now().isoformat(timespec="seconds")
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


# Patterns that indicate a write/modify operation (not a read)
WRITE_PATTERNS = [
    "mv ", "cp ", "rm ", "mkdir ", "touch ",
    "git add", "git commit", "git push", "git reset", "git checkout",
    "chmod ", "chown ",
    "curl -X POST", "curl -X PUT", "curl -X PATCH", "curl -X DELETE",
    "--data", "-d '", '-d "',
    "> ", ">> ",
    "pip install", "npm install",
    "python3 -c", "node -e",
]


def is_write_operation(data):
    """Check if this tool call is a write/modify operation."""
    tool_name = data.get("tool_name", "")

    # Agent dispatches always count (they do substantive work)
    if tool_name == "Agent":
        return True

    # For Bash, check if command looks like a write operation
    command = data.get("tool_input", {}).get("command", "").lower()
    return any(p.lower() in command for p in WRITE_PATTERNS)


def get_batch_limit():
    """Get current batch limit. Check override file first, then default."""
    try:
        with open(LIMIT_OVERRIDE_FILE, "r") as f:
            override = json.load(f)
        limit = int(override.get("limit", DEFAULT_LIMIT))
        # Sanity bounds: never below 10, never above 75
        return max(10, min(75, limit))
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return DEFAULT_LIMIT


def main():
    # Parse stdin to check if this is a write operation
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        pass

    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    # Only count write operations, not reads
    if not is_write_operation(data):
        sys.exit(0)

    state = load_state()
    limit = get_batch_limit()
    warn_at = int(limit * WARN_RATIO)

    state["count"] += 1
    count = state["count"]
    warnings_issued = state.get("warnings_issued", [])

    if count == warn_at and warn_at not in warnings_issued:
        warnings_issued.append(warn_at)
        state["warnings_issued"] = warnings_issued
        save_state(state)
        print(f"Approaching batch limit ({count}/{limit}). Plan your stopping point.")
    elif count >= limit:
        if limit not in warnings_issued:
            warnings_issued.append(limit)
            state["warnings_issued"] = warnings_issued
        save_state(state)
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"BATCH LIMIT EXCEEDED: {limit} write operations reached. Split into a new batch. (Override: echo '{{\"limit\": N, \"reason\": \"...\"}}' > {LIMIT_OVERRIDE_FILE})"
            }
        }
        print(json.dumps(output))
        sys.exit(0)
    else:
        save_state(state)

    sys.exit(0)


if __name__ == "__main__":
    main()
