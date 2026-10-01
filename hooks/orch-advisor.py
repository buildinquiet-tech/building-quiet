#!/usr/bin/env python3
"""PreToolUse hook — orch-advisor v0.1 (Phase 2, ORCH-MODE-ROLLOUT S348 2026-05-14).

Emits a soft advisory message before Bash/Edit/Write tool calls when the
prompt classifies as an economic work-type at confidence >= 0.6. Advisory
only — never blocks. Complements routing-advisor.py (hard-block, narrow
paths) by covering the broader Bash+Edit+Write surface with classifier
awareness.

Surfaces watched:
- Bash (command + description fields)
- Edit (new_string + first 200 chars of old_string)
- Write (content, truncated to 4000 chars)

Detection: classify_prompt() imported from bulk-burn-tracker.py via
importlib (hyphen-safe). Trigger when primary work-type in ECONOMIC_TYPES
and confidence >= 0.6.

Action posture: SOFT ADVISORY (exit 0 always). Emit structured message to
stderr when triggered. Routing-advisor hard-block paths are skipped
(no double-advise).

Failure mode: fail-open. Any exception → exit 0. Governance hooks must
never brick tool calls on a bug.

Bypass: export SENTINEL_OVERRIDE=bulk-burn to mute advisory. Muted fires
still logged with advisory_suppressed=true.

Fire log: ~/.echo/state/orch-advisor-fires.jsonl (JSONL, append-safe,
swallows OSError).

Cross-refs:
- Phase 1 telemetry: .claude/hooks/bulk-burn-tracker.py
- Hard-block layer: .claude/hooks/routing-advisor.py
- Doctrine: memory/feedback_hub_trust_spokes_economic.md
- Routing matrix: docs/_active/task-model-routing.md §11
- Spec: docs/_active/orchestrator-mode.md §"Hook upgrade path" L146
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path, PurePath

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HOOK_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path(os.environ.get(
    "CLAUDE_PROJECT_DIR",
    HOOK_DIR.parent.parent,
)).resolve()

WATCHED_TOOLS = {"Bash", "Edit", "Write"}

ECONOMIC_TYPES = {
    "drafting", "synthesis", "mechanical", "research", "classification", "structural",
}

CONFIDENCE_THRESHOLD = 0.6
SENTINEL_OVERRIDE_VAR = "SENTINEL_OVERRIDE"
SENTINEL_OVERRIDE_VALUE = "bulk-burn"

# Tier — see echo/reviews/orch-advisor-phase3-promotion-2026-05-28-S383.md
DEFAULT_TIER = "advisory"
ORCH_ADVISOR_TIER_VAR = "ORCH_ADVISOR_TIER"

FIRE_LOG = Path.home() / ".echo" / "state" / "orch-advisor-fires.jsonl"

# routing-advisor thresholds — mirrored to detect double-advise paths
_RA_GLOB_PATTERNS = ["scripts/*.py", "scripts/*.sh", ".claude/hooks/*.py"]
_RA_NESTED_PREFIX = "drafts"
_RA_WRITE_THRESHOLD = 500
_RA_EDIT_NEW_THRESHOLD = 300
_RA_EDIT_DELTA_THRESHOLD = 200

SPOKE_ROUTES = {
    "drafting":       "python3 scripts/kie_chat.py --model sonnet-4-6",
    "synthesis":      "python3 scripts/kie_chat.py --model sonnet-4-6",
    "research":       "multi-source: dispatch Gemini Deep Research / Explore subagent (Haiku)",
    "mechanical":     "Bash + native scripts (no LLM)",
    "classification": "kie.ai Haiku 4.5 OR inline if small",
    "structural":     "Explore/Plan subagent",
}


# ---------------------------------------------------------------------------
# Classifier import (hyphen-safe via importlib)
# ---------------------------------------------------------------------------

def _load_classify_prompt():
    """Load classify_prompt from bulk-burn-tracker.py (hyphens require importlib)."""
    spec = importlib.util.spec_from_file_location(
        "bbt", HOOK_DIR / "bulk-burn-tracker.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot locate bulk-burn-tracker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.classify_prompt


# ---------------------------------------------------------------------------
# Prompt extraction per tool
# ---------------------------------------------------------------------------

def extract_prompt(tool_name: str, tool_input: dict) -> str:
    """Return the text to classify for the given tool and its input."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "") or ""
        desc = tool_input.get("description", "") or ""
        return f"{cmd} {desc}".strip()
    if tool_name == "Edit":
        new_str = tool_input.get("new_string", "") or ""
        old_str = tool_input.get("old_string", "") or ""
        return f"{new_str} {old_str[:200]}".strip()
    if tool_name == "Write":
        content = tool_input.get("content", "") or ""
        return content[:4000]
    return ""


# ---------------------------------------------------------------------------
# Routing-advisor double-advise guard
# ---------------------------------------------------------------------------

def _ra_matches_watched(rel_path: str) -> bool:
    p = PurePath(rel_path)
    if rel_path == _RA_NESTED_PREFIX or rel_path.startswith(_RA_NESTED_PREFIX + "/"):
        return True
    try:
        if p.is_relative_to(_RA_NESTED_PREFIX):
            return True
    except AttributeError:
        pass
    return any(p.match(g) for g in _RA_GLOB_PATTERNS)


def routing_advisor_would_block(tool_name: str, tool_input: dict) -> bool:
    """Return True if routing-advisor.py would hard-block this call."""
    if tool_name not in ("Write", "Edit"):
        return False
    file_path = tool_input.get("file_path", "") or ""
    if not isinstance(file_path, str) or not file_path:
        return False
    p = Path(file_path)
    if not p.is_absolute():
        p = PROJECT_DIR / p
    try:
        rel_path = str(p.resolve(strict=False).relative_to(PROJECT_DIR))
    except (ValueError, OSError):
        return False
    if not _ra_matches_watched(rel_path):
        return False
    if tool_name == "Write":
        content = tool_input.get("content", "") or ""
        return len(content) > _RA_WRITE_THRESHOLD
    if tool_name == "Edit":
        new_str = tool_input.get("new_string", "") or ""
        old_str = tool_input.get("old_string", "") or ""
        delta = len(new_str) - len(old_str)
        return len(new_str) > _RA_EDIT_NEW_THRESHOLD or delta > _RA_EDIT_DELTA_THRESHOLD
    return False


# ---------------------------------------------------------------------------
# Advisory message
# ---------------------------------------------------------------------------

def advisory_message(tool_name: str, classification: dict) -> str:
    primary = classification.get("primary", "unknown")
    conf = classification.get("confidence", 0.0)
    matched = classification.get("matched_keywords", [])
    route = SPOKE_ROUTES.get(primary, "see docs/_active/task-model-routing.md §11")
    return (
        f"[ORCH-ADVISOR]\n"
        f"work_type: {primary} (conf={conf:.2f})\n"
        f"surface: {tool_name}\n"
        f"class: ECONOMIC_BURN\n"
        f"matched_verbs: {matched}\n"
        f"---\n"
        f"Spoke option: {route}\n"
        f"- drafting/synthesis → python3 scripts/kie_chat.py --model sonnet-4-6\n"
        f"- research → multi-source: dispatch Gemini Deep Research / Explore subagent (Haiku)\n"
        f"- mechanical → Bash + native scripts (no LLM)\n"
        f"- classification → kie.ai Haiku 4.5 OR inline if small\n"
        f"- structural → Explore/Plan subagent\n"
        f"Continue with Hub, or dispatch. Override mute: export SENTINEL_OVERRIDE=bulk-burn"
    )


# ---------------------------------------------------------------------------
# Fire log
# ---------------------------------------------------------------------------

def append_fire_log(entry: dict) -> None:
    """Append JSONL record to fire log; swallow OSError silently."""
    try:
        FIRE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(FIRE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if tool_name not in WATCHED_TOOLS:
        sys.exit(0)

    tool_input = payload.get("tool_input", {}) or {}
    prompt_text = extract_prompt(tool_name, tool_input)

    ra_blocks = routing_advisor_would_block(tool_name, tool_input)
    if ra_blocks:
        # routing-advisor will hard-block; don't double-advise
        sys.exit(0)

    try:
        classify_prompt = _load_classify_prompt()
        classification = classify_prompt(prompt_text)
    except Exception:
        sys.exit(0)

    primary = classification.get("primary", "unknown")
    confidence = classification.get("confidence", 0.0)

    is_economic = primary in ECONOMIC_TYPES and confidence >= CONFIDENCE_THRESHOLD

    sentinel = os.environ.get(SENTINEL_OVERRIDE_VAR, "").strip()
    suppressed = sentinel == SENTINEL_OVERRIDE_VALUE

    trigger_path = tool_input.get("file_path", "") if tool_name in ("Edit", "Write") else ""

    append_fire_log({
        "ts": datetime.now().isoformat(),
        "session_id": payload.get("session_id", ""),
        "tool": tool_name,
        "surface": tool_name,
        "classification": classification,
        "trigger_path": trigger_path,
        "prompt_len": len(prompt_text),
        "advisory_suppressed": suppressed,
        "routing_advisor_would_block": ra_blocks,
    })

    if is_economic and not suppressed:
        sys.stderr.write(advisory_message(tool_name, classification) + "\n")
        if os.environ.get(ORCH_ADVISOR_TIER_VAR, DEFAULT_TIER).lower() == "blocking":
            sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
