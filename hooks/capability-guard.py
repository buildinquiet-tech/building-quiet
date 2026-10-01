#!/usr/bin/env python3
"""Capability Guard — PreToolUse hook for Agent dispatch advisory.

Monitors Agent tool calls and checks the requested subagent_type against
the workspace-level capability matrix. Mismatches trigger an advisory flag
(stdout), never a block. Override proceeds are logged to sentinel-override-log.json.

Part of the Agent Capability Matrix system (S173).
See: .claude/skills/references/agent-capability-matrix.md

Three Rules:
  Rule 0: System Integrity — handled by zone-guard.py (not this hook)
  Rule 1: Capability Alignment — THIS HOOK (advisory only, exit 0 always)
  Rule 2: Operator Clause — override logging handled by sentinel-override-log.json
"""

import json
import sys
import os
from datetime import datetime

# --- Configuration ---

OVERRIDE_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sentinel-override-log.json")

# Subagent type → Capability type mapping
TYPE_MAP = {
    "Explore": "Explore",
    "Plan": "Plan",
    "general-purpose": "Execute",
    "superpowers:code-reviewer": "Verify",
    "statusline-setup": "Guide",
}

# Workspace-level capability matrix
# Key = workspace identifier, Value = set of allowed capability types
# "Brain" is default (VS Code Claude) — full authority
WORKSPACE_MATRIX = {
    "Brain": {"Explore", "Plan", "Execute", "Verify", "Guide", "General"},
    "Limb": {"Explore", "Execute", "Verify"},
    "Jarvis": {"Explore", "Execute", "Verify", "Guide"},
    "Ollama": {"Explore", "Verify"},
    "EchoPlatform": {"Explore", "Plan", "Execute", "Verify", "Guide"},
}

# Handoff suggestions per capability type
HANDOFF_MAP = {
    "Explore": "Route to Ollama (free) or Jarvis for bulk research.",
    "Plan": "This is strategy work. Route to Echo Brain for full context.",
    "Execute": "Route to Jarvis/PopeBot (mixed tokens) or Echo Brain.",
    "Verify": "Route to Ollama (free) for verification tasks.",
    "Guide": "Documentation work. Route to Jarvis (Guide-capable) or Brain.",
    "General": "General orchestration requires Echo Brain (CEO authority).",
}


def detect_workspace():
    """Detect current workspace from environment.

    Returns 'Brain' by default (VS Code Claude).
    Future: detect Jarvis/Ollama/Limb from env vars or session state.
    """
    # Check for workspace indicators
    workspace = os.environ.get("ECHO_WORKSPACE", "").lower()
    if workspace == "limb":
        return "Limb"
    elif workspace == "jarvis":
        return "Jarvis"
    elif workspace == "ollama":
        return "Ollama"
    elif workspace == "platform":
        return "EchoPlatform"
    return "Brain"


def map_subagent_type(subagent_type):
    """Map Claude Code subagent_type to capability type."""
    if subagent_type is None or subagent_type == "":
        return "Execute"  # default subagent_type is general-purpose
    return TYPE_MAP.get(subagent_type, "General")  # unknown → General


def log_override(workspace, skill, requested_type, allowed_types, reason=""):
    """Log a capability override to sentinel-override-log.json."""
    entry = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "type": "capability-override",
        "skill": skill,
        "requested_type": requested_type,
        "allowed_types": sorted(allowed_types),
        "workspace": workspace,
        "reason": reason,
    }

    try:
        if os.path.exists(OVERRIDE_LOG):
            with open(OVERRIDE_LOG, "r") as f:
                data = json.load(f)
        else:
            data = []

        if not isinstance(data, list):
            data = [data]

        data.append(entry)

        with open(OVERRIDE_LOG, "w") as f:
            json.dump(data, f, indent=2)
    except (json.JSONDecodeError, IOError):
        # Don't block on logging failures
        pass


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, IOError):
        sys.exit(0)  # Can't parse → pass silently

    tool_name = hook_input.get("tool_name", "")

    # Only monitor Agent tool calls
    if tool_name != "Agent":
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    subagent_type = tool_input.get("subagent_type", "")
    description = tool_input.get("description", "")

    # Map to capability type
    capability_type = map_subagent_type(subagent_type)

    # Detect workspace
    workspace = detect_workspace()

    # Check matrix
    allowed = WORKSPACE_MATRIX.get(workspace, WORKSPACE_MATRIX["Brain"])

    if capability_type in allowed:
        # Allowed — pass silently
        sys.exit(0)

    # --- MISMATCH: Advisory Flag ---
    handoff = HANDOFF_MAP.get(capability_type, "Check agent-capability-matrix.md for routing.")

    flag_msg = (
        f"\n⚠️ CAPABILITY FLAG: {workspace} workspace dispatching a {capability_type} agent.\n"
        f"Allowed types for {workspace}: {', '.join(sorted(allowed))}.\n"
        f"Requested: {capability_type} (subagent_type: {subagent_type or 'general-purpose'})\n"
        f"Task: {description}\n"
        f"→ {handoff}\n"
        f"→ To hand off: use /great-prompt to formulate, then route to correct workspace.\n"
        f"→ To override: proceed — override will be logged.\n"
    )

    # Output advisory (stdout — does not block)
    print(flag_msg)

    # Pre-log the override (will appear in sentinel-override-log.json)
    # The override is logged at flag time because the hook cannot block;
    # if Echo/the operator proceeds, the log entry exists for weekly review.
    log_override(
        workspace=workspace,
        skill="unknown",  # Skill detection is Phase 2 (option b)
        requested_type=capability_type,
        allowed_types=list(allowed),
        reason=f"Agent dispatch: {description}",
    )

    # Always exit 0 — advisory only, never block
    sys.exit(0)


if __name__ == "__main__":
    main()
