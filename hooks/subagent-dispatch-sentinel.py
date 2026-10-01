#!/usr/bin/env python3
"""Subagent Dispatch Sentinel — Stop hook.

Enforces dispatch-artifact pattern for state-changing subagent invocations.
Triggers ONLY on Agents whose prompts indicate external state work
(publish, schedule, send, deploy, ship, post, render, redact, etc.) —
not on read-only research / Explore / Plan agents.

Trigger: Stop hook fires after assistant emits a stop signal.
Detection: Any tool_use named "Agent" whose prompt contains a state-change
verb. (Read-only Explore/Plan dispatches are exempt by signal, not by flag.)
Required artifact: file under `echo/dispatches/` Edit'd or Written THIS session.

Override: SENTINEL_OVERRIDE=dispatch
"""

import json
import sys
import os
import re

DISPATCHES_DIR_TOKEN = "echo/dispatches/"

# State-change verbs that suggest the subagent will mutate external state
STATE_CHANGE_VERBS = re.compile(
    r"\b(publish|schedule|send|deploy|ship|post|render|redact|delete|"
    r"upload|migrate|rotate|cancel|provision|charge|commit|push|"
    r"merge|fire|dispatch|execute|--apply|run\s+the\s+(script|dispatch))\b",
    re.IGNORECASE,
)

# Subagent types that are read-only by nature — exempt
EXEMPT_SUBAGENT_TYPES = {
    "Explore",
    "Plan",
    "statusline-setup",
    "claude-code-guide",
}


def iter_tool_uses(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") != "assistant":
                    continue
                msg = evt.get("message", {})
                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        yield blk.get("name", ""), blk.get("input", {}) or {}
    except (OSError, UnicodeDecodeError):
        return


def detect_dispatch_activity(transcript_path):
    """Return (state_change_dispatch, dispatch_artifact, evidence)."""
    state_change_dispatch = False
    dispatch_artifact = False
    evidence = None

    for name, tin in iter_tool_uses(transcript_path):
        if name == "Agent":
            subagent_type = str(tin.get("subagent_type", ""))
            if subagent_type in EXEMPT_SUBAGENT_TYPES:
                continue
            prompt = str(tin.get("prompt", ""))
            description = str(tin.get("description", ""))
            combined = prompt + " " + description
            if STATE_CHANGE_VERBS.search(combined):
                state_change_dispatch = True
                m = STATE_CHANGE_VERBS.search(combined)
                evidence = (
                    f"Agent (subagent_type={subagent_type or 'general-purpose'}, "
                    f"verb='{m.group(0).lower()}', desc='{description[:60]}')"
                )

        if name in ("Edit", "Write", "NotebookEdit"):
            fp = str(tin.get("file_path", ""))
            if DISPATCHES_DIR_TOKEN in fp:
                dispatch_artifact = True

    return state_change_dispatch, dispatch_artifact, evidence


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "dispatch":
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)

    transcript_path = data.get("transcript_path", "")
    state_change, artifact, evidence = detect_dispatch_activity(transcript_path)

    if not state_change:
        sys.exit(0)

    if artifact:
        sys.exit(0)

    msg = (
        f"[DISPATCH GATE] State-changing subagent invocation detected this "
        f"session ({evidence}) but no file under `echo/dispatches/` was "
        f"Edit'd or Written.\n\n"
        f"Subagents that mutate external state need a paper trail. The "
        f"prompt instructions live in conversation only — if the agent "
        f"crashes, returns weird, or runs in a parallel surface (Mac App / "
        f"VS Code split), the next session has no record of what was asked.\n\n"
        f"PREFERRED: Pre-tag pattern (log dispatch as it moves through flow):\n"
        f"  1. BEFORE Agent call → write `echo/dispatches/{{slug}}-DISPATCHED.md` "
        f"(prompt, rationale, expected output, cost-est, requested-at)\n"
        f"  2. Run the Agent\n"
        f"  3. AFTER return → write `echo/dispatches/{{slug}}-RESULTS.md` "
        f"(actual output, exit status, anomalies, follow-up actions)\n"
        f"  Why: parallel-surface safe (Mac App ↔ VS Code), crash-safe, "
        f"makes subagent runs reviewable.\n\n"
        f"FALLBACK: Post-log pattern — write a single combined RESULTS.md "
        f"with both the dispatch instructions and outcome.\n\n"
        f"Exempt subagent types (no dispatch file needed): "
        f"{sorted(EXEMPT_SUBAGENT_TYPES)}\n"
        f"Exempt verbs: read-only research, exploration, code review, "
        f"planning. State-change verbs that trigger this gate: publish, "
        f"schedule, send, deploy, ship, post, render, redact, delete, "
        f"upload, migrate, rotate, cancel, provision, charge, commit, "
        f"push, merge, fire, dispatch, execute, --apply.\n\n"
        f"Override (this turn only): SENTINEL_OVERRIDE=dispatch"
    )

    output = {
        "decision": "block",
        "reason": msg,
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
