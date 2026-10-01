#!/usr/bin/env python3
"""PostToolUse hook: warns when context pressure is building.

S330 D4d upgrade: replaces tool-call-count thresholds with real
token-based ctx-% read from scripts/ctx_status.py. Keeps tool-call
counter as fallback when ctx_status data is unavailable.

Wrap-signal ladder (per docs/_active/orchestrator-mode.md):
  green    <60%   silent
  yellow   60-79% advise wrap soon
  red      80-94% recommend /save-state
  critical 95%+   auto-save instruction (proactive)

Only prints on tier transitions (entering yellow/red/critical for
the first time, or re-entering after stale reset). Avoids spamming
every tool call.

Also covers Protocol 8 (Skipped-Closing Safety Net) via tool-call
fallback when ctx_status fails.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

STATE_FILE = "/tmp/echo-context-pressure.json"
STALE_HOURS = 4  # Reset after 4 hours (new session assumed)

# Token-based thresholds (primary)
TIER_ADVISE = "yellow"     # 60-79%
TIER_RECOMMEND = "red"     # 80-94%
TIER_CRITICAL = "critical" # 95%+

# Tool-call fallback thresholds (used if ctx_status unavailable)
WARN_THRESHOLD = 150
CRITICAL_THRESHOLD = 200
REMIND_INTERVAL = 25


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        last = datetime.fromisoformat(state.get("last_updated", "2000-01-01"))
        if (datetime.now() - last).total_seconds() > STALE_HOURS * 3600:
            return _empty_state()
        return state
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return _empty_state()


def _empty_state():
    return {
        "call_count": 0,
        "last_tier": "green",
        "warned": False,
        "critical": False,
        "last_updated": "",
    }


def save_state(state):
    state["last_updated"] = datetime.now().isoformat(timespec="seconds")
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def get_ctx_status_safe():
    """Import ctx_status from scripts/ if available, return status dict or None."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    scripts_dir = Path(project_dir) / "scripts" if project_dir else Path("scripts")
    if not (scripts_dir / "ctx_status.py").exists():
        return None
    sys.path.insert(0, str(scripts_dir))
    try:
        import ctx_status  # noqa: PLC0415
        status = ctx_status.get_ctx_status()
        if status.get("error"):
            return None
        return status
    except Exception:
        return None
    finally:
        if str(scripts_dir) in sys.path:
            sys.path.remove(str(scripts_dir))


def print_tier_message(tier, pct, tokens):
    """Print the wrap-signal message for the given tier transition."""
    if tier == "yellow":
        print("=" * 60)
        print(f">>> CONTEXT YELLOW ({pct}% / {tokens:,} tokens)")
        print("    Conversation is building. Consider wrapping soon.")
        print("    /save-state when natural break comes.")
        print("=" * 60)
    elif tier == "red":
        print("=" * 60)
        print(f">>> CONTEXT RED ({pct}% / {tokens:,} tokens)")
        print("    /save-state recommended now. Don't start new threads.")
        print("    1. Wrap current task")
        print("    2. /save-state to commit handoff")
        print("    3. Open fresh tab for next topic")
        print("=" * 60)
    elif tier == "critical":
        print("=" * 60)
        print(f">>> CONTEXT CRITICAL ({pct}% / {tokens:,} tokens)")
        print("    AUTO-SAVE NOW. Compression imminent.")
        print("    1. /save-state IMMEDIATELY")
        print("    2. Tell the operator: 'Context heavy. State saved. New tab when ready.'")
        print("=" * 60)


def fallback_tool_call_counter(state):
    """Original tool-call-count behavior, used when ctx_status unavailable."""
    state["call_count"] = state.get("call_count", 0) + 1
    count = state["call_count"]

    if count == WARN_THRESHOLD:
        print("=" * 60)
        print(f">>> CONTEXT PRESSURE WARNING ({count} tool calls)")
        print("    [fallback mode — ctx_status.py unavailable]")
        print("    Session is getting long. Start preparing handoff:")
        print("    1. Write handoff to echo/handoffs/YYYY-MM-DD.md")
        print("    2. Commit all modified files")
        print("    3. Update memory if needed")
        print("=" * 60)
        state["warned"] = True

    elif count == CRITICAL_THRESHOLD:
        print("=" * 60)
        print(f">>> CRITICAL: CONTEXT COMPRESSION IMMINENT ({count} tool calls)")
        print("    [fallback mode — ctx_status.py unavailable]")
        print("    AUTO-SAVE NOW. This is not optional.")
        print("=" * 60)
        state["critical"] = True

    elif count > CRITICAL_THRESHOLD and (count - CRITICAL_THRESHOLD) % REMIND_INTERVAL == 0:
        print(f">>> CONTEXT CRITICAL ({count} calls). Save state and wrap up.")


def main():
    try:
        sys.stdin.read()
    except Exception:
        pass

    state = load_state()
    status = get_ctx_status_safe()

    if status is None:
        # Fallback: tool-call counter
        fallback_tool_call_counter(state)
    else:
        # Primary: token-based tier
        tier = status["tier"]
        pct = status["pct"]
        tokens = status["tokens"]
        last_tier = state.get("last_tier", "green")

        # Print only on tier escalation (green -> yellow -> red -> critical)
        tier_rank = {"green": 0, "yellow": 1, "red": 2, "critical": 3, "unknown": 0}
        if tier_rank.get(tier, 0) > tier_rank.get(last_tier, 0):
            print_tier_message(tier, pct, tokens)
        # Also re-warn at critical every tool call (current behavior preserved)
        elif tier == "critical" and last_tier == "critical":
            print(f">>> CONTEXT CRITICAL ({pct}%). Auto-save threshold passed. Wrap NOW.")

        state["last_tier"] = tier
        # Keep tool-call counter running for diagnostic
        state["call_count"] = state.get("call_count", 0) + 1

    save_state(state)


if __name__ == "__main__":
    main()
