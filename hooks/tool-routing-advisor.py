#!/usr/bin/env python3
"""
Tool Routing Advisor — classifies tool calls against the Task-Tier matrix
(docs/_active/task-model-routing.md) and surfaces model/effort advisories.

Events handled:
  PostToolUse  — classify the tool call, update session state, emit advisory on threshold
  Stop         — on severe Tier 6 accumulation, emit {"decision":"block"} to halt the stop

Advisory-only on PostToolUse (no tool blocking). Stop blocking only on severe.

State files (auto-created):
  .claude/hooks/tool-routing-session.json  (session-scoped, overwritten each session)
  .claude/hooks/tool-routing-log.json      (cross-session, append-only for /closing)

Dismissal: "dismiss routing" -> Echo edits session file to flip dismissed flag.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ----- Paths ---------------------------------------------------------------
PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
SESSION_STATE_FILE = PROJECT_DIR / ".claude/hooks/tool-routing-session.json"
CROSS_SESSION_LOG = PROJECT_DIR / ".claude/hooks/tool-routing-log.json"

# ----- Thresholds (per spec) -----------------------------------------------
LIGHT_TIER6_COUNT = 5
SEVERE_TIER6_COUNT = 15
TRANSITION_STREAK = 3

# ----- Tier classification patterns ----------------------------------------
TIER_0_SKILL_PREFIXES = ("superpowers:",)
TIER_0_WRITE_PATH_FRAGMENTS = ("/docs/superpowers/specs/", "/docs/superpowers/plans/")
TIER_2_TOOL_NAMES = {"WebFetch", "WebSearch"}
TIER_2_MCP_PREFIXES = ("mcp__firecrawl__",)
TIER_2_MCP_EXACT = {"mcp__plugin_context-mode_context-mode__ctx_fetch_and_index"}
TIER_6_BASH_FIRST_WORDS = {"ls", "grep", "find", "wc", "cat", "head", "tail"}
TIER_6_GIT_SUBCOMMANDS = {"status", "log", "diff", "show"}
TIER_6_EDIT_MAX_OLDSTRING = 20


def classify_tool_call(data: dict) -> int:
    """Classify a tool call into tier 0/1/2/6 or 3 (neutral fallback).

    Priority: Tier 0 (strategy) > Tier 2 (research) > Tier 1 (monitoring) >
    Tier 6 (grunt) > Tier 3 (default).
    """
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}

    # --- Tier 0: Strategy ---
    if tool_name == "Skill":
        return 0
    if tool_name == "Write":
        path = tool_input.get("file_path", "")
        if any(frag in path for frag in TIER_0_WRITE_PATH_FRAGMENTS):
            return 0

    # --- Tier 2: Research ---
    if tool_name in TIER_2_TOOL_NAMES:
        return 2
    if any(tool_name.startswith(p) for p in TIER_2_MCP_PREFIXES):
        return 2
    if tool_name in TIER_2_MCP_EXACT:
        return 2

    # --- Tier 1: Monitoring (MCP get_* / list_*) ---
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        method = parts[-1] if len(parts) >= 3 else ""
        if method.startswith("get_") or method.startswith("list_"):
            return 1

    # --- Tier 6: Grunt ---
    if tool_name == "Edit":
        old_string = tool_input.get("old_string", "") or ""
        replace_all = tool_input.get("replace_all", False)
        if replace_all and len(old_string) < TIER_6_EDIT_MAX_OLDSTRING:
            return 6
    if tool_name == "Bash":
        cmd = (tool_input.get("command", "") or "").strip().split()
        if cmd:
            first = cmd[0]
            if first in TIER_6_BASH_FIRST_WORDS:
                return 6
            if first == "git" and len(cmd) > 1 and cmd[1] in TIER_6_GIT_SUBCOMMANDS:
                return 6

    # --- Default: Tier 3 (neutral) ---
    return 3


def load_session_state(incoming_session_id: str | None = None) -> dict:
    """Load session state. Rotates state when incoming session_id differs from stored.

    Three behaviors:
      - stored_id is None: adopt incoming_session_id, keep counts (first turn of a fresh state)
      - stored_id matches incoming: continue (normal mid-session call)
      - stored_id differs from incoming: archive previous session to cross-session log,
        reset state with the new session_id

    If incoming_session_id is None (hook event lacks the field), state stays as-is — no rotation.
    """
    default = {
        "session_id": incoming_session_id,
        "session_start": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tier_counts": {"0": 0, "1": 0, "2": 0, "3": 0, "6": 0},
        "tier_history": [],
        "dismissed_until_session_end": False,
        "last_advisory_fired": None,
        "advisory_fires": 0,
    }
    try:
        with open(SESSION_STATE_FILE, "r") as f:
            state = json.load(f)
        for k, v in default.items():
            state.setdefault(k, v)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

    stored_id = state.get("session_id")
    if incoming_session_id and stored_id != incoming_session_id:
        # Rotation: stored != incoming OR stored is None (legacy pre-patch state).
        # Archive previous run to cross-session log, return fresh defaults.
        append_to_cross_session_log(state)
        return default
    return state


def save_session_state(state: dict) -> None:
    """Persist session state to disk. Creates parent dir if needed."""
    SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SESSION_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def should_fire_advisory(state: dict, current_tier: int):
    """Return (severity, reason) or (None, '') if no advisory should fire.

    Severity progression: severe > medium > light. Each severity fires at most once
    per session unless dismissed. Dismissed state suppresses all advisories.
    """
    if state.get("dismissed_until_session_end"):
        return (None, "")

    tier6_count = state["tier_counts"].get("6", 0)
    last_fired = state.get("last_advisory_fired")
    history = state.get("tier_history", [])

    # --- Severe: 15+ Tier 6 ops ---
    if tier6_count >= SEVERE_TIER6_COUNT and last_fired != "severe":
        return ("severe", f"{tier6_count} tier-6 ops this session — severe mismatch")

    # --- Medium: task-tier transition ---
    if len(history) >= TRANSITION_STREAK + 1:
        recent = history[-(TRANSITION_STREAK + 1):]
        prior_streak = recent[:-1]
        current_op = recent[-1]
        if current_op != prior_streak[0] and all(t == prior_streak[0] for t in prior_streak):
            transition_key = f"medium_{prior_streak[0]}_to_{current_op}"
            if last_fired != transition_key:
                return (
                    "medium",
                    f"task switch: last {TRANSITION_STREAK} ops were tier {prior_streak[0]}, "
                    f"current op is tier {current_op} — model switch recommended"
                )

    # --- Light: 5+ Tier 6 ops, only fires once ---
    if tier6_count >= LIGHT_TIER6_COUNT and last_fired is None and current_tier == 6:
        return ("light", f"{tier6_count} tier-6 ops — consider /model haiku for grunt work")

    return (None, "")


def advisory_output(severity: str, state: dict, reason: str) -> str:
    """Generate the advisory string per spec Advisory Format section."""
    counts = state.get("tier_counts", {})
    counts_str = (
        f"Tier 0: {counts.get('0', 0)}, "
        f"Tier 1: {counts.get('1', 0)}, "
        f"Tier 2: {counts.get('2', 0)}, "
        f"Tier 6: {counts.get('6', 0)}"
    )

    if severity == "severe":
        return (
            "\n[MODEL ROUTING — SEVERE]\n"
            f"{reason}\n"
            f"Session counts: {{{counts_str}}}\n"
            "Echo should now invoke AskUserQuestion with options: "
            "Switch model / Stay on current / Dismiss for session.\n"
            "Matrix reference: docs/_active/task-model-routing.md §Task-Tier Routing Table\n"
        )

    if severity == "medium":
        return (
            "\n[MODEL ROUTING] Task switch detected:\n"
            f"  {reason}\n"
            "  Recommendation: review model choice against docs/_active/task-model-routing.md\n"
            "  Dismiss for session: (reply 'dismiss routing' in next turn)\n"
        )

    # light
    return (
        "\n[MODEL ROUTING ADVISORY]\n"
        f"Pattern: {reason}\n"
        f"Session counts: {{{counts_str}}}\n"
        "Recommended action:\n"
        "  /model haiku          # 95% cheaper for grunt work\n"
        "  OR route to PopeBot   # BLOCKED on A-061 tunnel fix\n"
        "Matrix reference: docs/_active/task-model-routing.md §Task-Tier Routing Table\n"
        "Dismiss for session: (reply 'dismiss routing' in next turn)\n"
    )


def append_to_cross_session_log(state: dict) -> None:
    """Append a final summary entry to the cross-session log (for /closing)."""
    CROSS_SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CROSS_SESSION_LOG, "r") as f:
            entries = json.load(f)
        if not isinstance(entries, list):
            entries = []
    except (FileNotFoundError, json.JSONDecodeError):
        entries = []

    entry = {
        "session_id": state.get("session_id"),
        "session_start": state.get("session_start"),
        "session_end": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tier_counts": dict(state.get("tier_counts", {})),
        "tier_history_length": len(state.get("tier_history", [])),
        "advisory_fires": state.get("advisory_fires", 0),
        "dismissed": state.get("dismissed_until_session_end", False),
    }
    entries.append(entry)

    with open(CROSS_SESSION_LOG, "w") as f:
        json.dump(entries, f, indent=2)


def handle_post_tool_use(data: dict) -> None:
    """Classify the tool call, update session state, emit advisory on threshold."""
    state = load_session_state(data.get("session_id"))
    tier = classify_tool_call(data)

    key = str(tier)
    state["tier_counts"][key] = state["tier_counts"].get(key, 0) + 1
    history = state.get("tier_history", [])
    history.append(tier)
    if len(history) > 50:
        history = history[-50:]
    state["tier_history"] = history

    severity, reason = should_fire_advisory(state, current_tier=tier)
    if severity:
        out = advisory_output(severity, state, reason)
        print(out, file=sys.stderr)
        if severity == "medium" and len(history) >= 2:
            state["last_advisory_fired"] = f"medium_{history[-2]}_to_{tier}"
        else:
            state["last_advisory_fired"] = severity
        state["advisory_fires"] = state.get("advisory_fires", 0) + 1

    save_session_state(state)


def handle_stop(data: dict) -> None:
    """On severe mismatch (15+ Tier 6, not dismissed), emit block decision.
    Otherwise, append final session summary to cross-session log and allow stop."""
    state = load_session_state(data.get("session_id"))
    tier6 = state["tier_counts"].get("6", 0)
    dismissed = state.get("dismissed_until_session_end", False)

    if tier6 >= SEVERE_TIER6_COUNT and not dismissed:
        decision = {
            "decision": "block",
            "reason": (
                f"Model routing severe: {tier6} Tier 6 ops this session. "
                "Invoke AskUserQuestion: Switch model / Stay / Dismiss routing."
            ),
        }
        print(json.dumps(decision))
        return

    append_to_cross_session_log(state)


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        sys.exit(0)

    event = data.get("hook_event_name", "")
    if event == "PostToolUse":
        handle_post_tool_use(data)
    elif event == "Stop":
        handle_stop(data)
    sys.exit(0)


if __name__ == "__main__":
    main()
