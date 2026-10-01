#!/usr/bin/env python3
"""Stop hook — bulk-burn telemetry v2 (S343 PM rebuild).

Per-turn accounting + multi-surface matchers + confidence-aware classifier +
economic severity weighting + miscategorization class (doctrine S343 PM).

Doctrine pair (memory/feedback_hub_trust_spokes_economic.md):
- Hub = trust anchor for truth-critical / governance / judgment work.
- Spokes = economic execution lanes for drafting / synthesis / batch.
- Routing decision branches on work-TYPE first, cost second.

Two failure classes flagged:
- ECONOMIC_BURN: economic work-type detected on hub without spoke dispatch.
- TRUST_LEAK: trust-anchor work-type dispatched to spoke (rare; surfaces
  the inverse miscat that "cheap is good" framing misses).

Surfaces watched (S343 Phase A1):
- Agent / Task subagent dispatches (existing)
- mcp__plugin_context-mode_context-mode__ctx_batch_execute / ctx_execute* / ctx_fetch_and_index
- mcp__kie-ai__* (paid spoke, counts as kie marker)
- Bash matchers: curl to api.anthropic.com OR api.kie.ai, python kie_chat.py,
  python urllib direct to paid endpoints

State dir: ~/.echo/state/ (S343 A4 migration from /tmp/).
Telemetry only — never blocks. /closing Phase 2f surfaces accumulated flags.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = os.environ.get(
    "CLAUDE_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

# ----- State dir (S343 A4) -------------------------------------------------
STATE_DIR = Path(os.path.expanduser("~/.echo/state"))
STATE_FILE = STATE_DIR / "bulk-burn-tracker.json"
CLASSIFIER_LOG = STATE_DIR / "classifier-cache.jsonl"
# Legacy path configurable via env for test isolation; default = real /tmp.
LEGACY_STATE_FILE = os.environ.get(
    "ECHO_BULK_BURN_LEGACY_STATE",
    "/tmp/echo-bulk-burn-tracker.json",
)

# ----- Output sink (existing — telemetry summary) --------------------------
LOG_FILE = os.path.join(PROJECT_DIR, "memory", "max-usage-log.md")
SECTION_HEADER = "## Bulk-Burn Flags (auto)"

# ----- Threshold (per S250; tunable post-backfill) -------------------------
BULK_PROMPT_THRESHOLD = 800

# ----- Tool-pattern matchers (Phase A1) ------------------------------------
SUBAGENT_TOOLS = {"Agent", "Task"}
HUB_HEAVY_MCP = {
    "mcp__plugin_context-mode_context-mode__ctx_batch_execute",
    "mcp__plugin_context-mode_context-mode__ctx_execute",
    "mcp__plugin_context-mode_context-mode__ctx_execute_file",
    "mcp__plugin_context-mode_context-mode__ctx_fetch_and_index",
    "mcp__context-mode__ctx_batch_execute",
    "mcp__context-mode__ctx_execute",
    "mcp__context-mode__ctx_execute_file",
    "mcp__context-mode__ctx_fetch_and_index",
}
SPOKE_MCP_PREFIX = "mcp__kie-ai__"
BASH_KIE_CHAT = re.compile(r"kie_chat\.py")
# S347 D-2026-0513-001: gemini_chat.py + kimi_chat.py are first-class spoke wrappers.
BASH_GEMINI_CHAT = re.compile(r"gemini_chat\.py")
BASH_KIMI_CHAT = re.compile(r"kimi_chat\.py")
BASH_CURL_PAID = re.compile(r"curl[^\n]*api\.(?:anthropic|kie)\.com", re.IGNORECASE)
BASH_PYTHON_URLLIB_PAID = re.compile(
    r"python[^\n]*urllib[^\n]*(?:anthropic|kie\.ai)", re.IGNORECASE
)

# ----- Classifier (Phase A2) -----------------------------------------------
# Categories per orchestrator-mode.md Phase 1 spec (7 work-types).
CLASSIFIER_KEYWORDS = {
    "drafting": [
        "draft", "write", "compose", "generate copy", "create caption",
        "create hook", "create body", "create post", "write post",
        "longform", "long-form", "chapter",
    ],
    "synthesis": [
        "synthesize", "synthesise", "consolidate", "merge", "integrate",
        "fold in", "fold-in", "summarize", "summarise", "distill",
    ],
    "mechanical": [
        "rename", "refactor", "reformat", "format-pass", "convert",
        "batch", "find and replace", "sed", "regex replace",
        "bulk update", "mechanical",
        # Ops/verification — S370 classifier tune. "verify" alone caused
        # 41/55 short-Bash fires to land in judgment. Operational verification
        # (pytest, git status, health checks) is mechanical, not judgment.
        "verify", "run tests", "run script", "run the", "build",
        "install", "deploy", "clean", "commit", "push", "pull", "check",
    ],
    "structural": [
        "restructure", "reorganize", "reorganise", "migrate", "schema",
        "redesign", "reshape",
        # Dev implementation patterns common in Edit/Write contexts.
        "implement", "add feature", "fix bug", "patch", "debug",
        "update the", "add the", "wire up",
    ],
    "research": [
        "research", "investigate", "explore", "audit", "scan",
        "gather", "scrape", "competitor", "lookup", "find sources",
        "find evidence", "survey", "reconnaissance", "recon",
    ],
    "classification": [
        "classify", "categorize", "categorise", "label", "tag",
        "sort into", "bucket", "triage",
    ],
    "judgment": [
        "review", "evaluate", "assess", "score", "grade",
        "decide", "recommend", "judge", "approve", "verdict",
        "validate", "verify",
    ],
}

# Economic = appropriate for spoke. Trust-anchor = belongs on hub.
ECONOMIC_TYPES = {"drafting", "synthesis", "mechanical", "structural", "research", "classification"}
TRUST_ANCHOR_TYPES = {"judgment"}

# ----- Severity (Phase A3) -------------------------------------------------
# Model detection from prompt/tool input. Premium = ~cost multiplier vs spoke.
MODEL_TIER = {
    "opus-4-7": "CRITICAL",   # ~10× hub vs spoke when Max-saturated
    "claude-opus-4-7": "CRITICAL",
    "opus-4-6": "HIGH",
    "claude-opus-4-6": "HIGH",
    "sonnet-4-6": "HIGH",     # ~72% premium vs spoke
    "claude-sonnet-4-6": "HIGH",
    "haiku-4-5": "LOW",       # ~65% premium small absolute
    "claude-haiku-4-5": "LOW",
}
DEFAULT_SEVERITY = "HIGH"  # unknown model → assume drafting class default


# ----- State I/O -----------------------------------------------------------
def ensure_state_dir() -> None:
    """Create ~/.echo/state/ if missing; migrate legacy /tmp state once."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    # One-time migration: copy legacy state if new state file does not exist.
    if not STATE_FILE.exists() and os.path.exists(LEGACY_STATE_FILE):
        try:
            with open(LEGACY_STATE_FILE) as src:
                legacy = src.read()
            STATE_FILE.write_text(legacy)
        except OSError:
            pass


def load_state() -> dict:
    ensure_state_dir()
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # Fall back to legacy path if reading fails during the transition.
        try:
            with open(LEGACY_STATE_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}


def save_state(state: dict) -> None:
    ensure_state_dir()
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def append_classifier_log(entry: dict) -> None:
    ensure_state_dir()
    try:
        with open(CLASSIFIER_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# ----- Classifier (Phase A2) -----------------------------------------------
def classify_prompt(prompt: str) -> dict:
    """Return {primary, secondary, confidence, rationale, matched_keywords}.

    Confidence = primary_matches / total_matches (clamped 0..1). When no
    keyword matches, returns confidence 0.0 with primary='unknown'. Phase C
    advisor gates on confidence > 0.7 (tunable post-Phase-B data).
    """
    if not prompt or not isinstance(prompt, str):
        return {
            "primary": "unknown",
            "secondary": None,
            "confidence": 0.0,
            "rationale": "empty prompt",
            "matched_keywords": [],
        }
    lower = prompt.lower()
    scores: dict[str, list[str]] = {}
    for category, keywords in CLASSIFIER_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in lower]
        if hits:
            scores[category] = hits
    if not scores:
        return {
            "primary": "unknown",
            "secondary": None,
            "confidence": 0.0,
            "rationale": "no classifier keywords matched",
            "matched_keywords": [],
        }
    ranked = sorted(scores.items(), key=lambda kv: len(kv[1]), reverse=True)
    primary, primary_hits = ranked[0]
    secondary = ranked[1][0] if len(ranked) > 1 else None
    total_matches = sum(len(v) for v in scores.values())
    confidence = round(len(primary_hits) / total_matches, 2) if total_matches else 0.0
    all_matched = sorted({kw for hits in scores.values() for kw in hits})
    rationale = (
        f"matched verbs {all_matched[:6]}; primary={primary} "
        f"({len(primary_hits)}/{total_matches})"
    )
    return {
        "primary": primary,
        "secondary": secondary,
        "confidence": confidence,
        "rationale": rationale,
        "matched_keywords": all_matched,
    }


# ----- Severity (Phase A3) -------------------------------------------------
def detect_model(prompt: str, tool_input: dict) -> str | None:
    """Find a model identifier in prompt or tool input. Returns canonical key."""
    haystack_parts = []
    if isinstance(prompt, str):
        haystack_parts.append(prompt)
    if isinstance(tool_input, dict):
        for value in tool_input.values():
            if isinstance(value, str):
                haystack_parts.append(value)
    haystack = " ".join(haystack_parts).lower()
    for key in MODEL_TIER:
        if key in haystack:
            return key
    return None


def severity_for(model: str | None, work_type: str) -> str:
    if model and model in MODEL_TIER:
        return MODEL_TIER[model]
    # No model declared — infer by work-type defaults.
    if work_type == "judgment":
        return "HIGH"  # judgment on Opus 4.7 hub when economic alt existed
    if work_type in ("drafting", "synthesis"):
        return "HIGH"
    if work_type in ("classification",):
        return "LOW"
    return DEFAULT_SEVERITY


# ----- Miscategorization class (S343 PM doctrine) --------------------------
def miscategorization_class(work_type: str, on_spoke: bool, confidence: float) -> str:
    """Return one of: ECONOMIC_BURN | TRUST_LEAK | OK | UNCLASSIFIED."""
    if confidence < 0.5:
        return "UNCLASSIFIED"
    if on_spoke and work_type in TRUST_ANCHOR_TYPES:
        return "TRUST_LEAK"
    if not on_spoke and work_type in ECONOMIC_TYPES:
        return "ECONOMIC_BURN"
    return "OK"


# ----- Transcript parser ---------------------------------------------------
def _is_bulk_subagent(tool_name: str, tool_input: dict) -> tuple[bool, str]:
    """Return (is_bulk, prompt_text). Bulk = prompt over threshold."""
    if tool_name not in SUBAGENT_TOOLS:
        return False, ""
    prompt = tool_input.get("prompt", "")
    if not isinstance(prompt, str):
        return False, ""
    return len(prompt) > BULK_PROMPT_THRESHOLD, prompt


def _is_hub_heavy_mcp(tool_name: str) -> bool:
    return tool_name in HUB_HEAVY_MCP


def _is_spoke_mcp(tool_name: str) -> bool:
    return tool_name.startswith(SPOKE_MCP_PREFIX)


def _bash_signature(command: str) -> str | None:
    """Classify Bash command. Returns one of:
    'kie_chat' (spoke marker), 'curl_paid' (hub burn via curl),
    'urllib_paid' (hub burn via urllib), or None.
    """
    if not isinstance(command, str):
        return None
    if BASH_KIE_CHAT.search(command) or BASH_GEMINI_CHAT.search(command) or BASH_KIMI_CHAT.search(command):
        return "kie_chat"
    if BASH_CURL_PAID.search(command):
        return "curl_paid"
    if BASH_PYTHON_URLLIB_PAID.search(command):
        return "urllib_paid"
    return None


def parse_transcript(path: str) -> dict:
    """Walk the session transcript and tally tool-use events.

    Returns counters dict; caller computes per-turn deltas vs prior state.
    """
    counters = {
        "sub_total": 0,
        "bulk_total": 0,
        "hub_heavy_mcp": 0,
        "spoke_mcp": 0,
        "kie_chat": 0,
        "curl_paid": 0,
        "urllib_paid": 0,
        # Per-event details so we can classify the NEW events on flag emission.
        "events": [],  # list of {tool, prompt, model, on_spoke}
    }
    if not path or not os.path.exists(path):
        return counters
    try:
        with open(path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                content = entry.get("message", {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use":
                        continue
                    tool = block.get("name", "") or ""
                    inp = block.get("input", {}) or {}

                    is_bulk, prompt = _is_bulk_subagent(tool, inp)
                    if tool in SUBAGENT_TOOLS:
                        counters["sub_total"] += 1
                        if is_bulk:
                            counters["bulk_total"] += 1
                            counters["events"].append({
                                "tool": tool,
                                "prompt": prompt,
                                "on_spoke": False,
                                "surface": "subagent",
                            })
                        continue

                    if _is_hub_heavy_mcp(tool):
                        counters["hub_heavy_mcp"] += 1
                        # ctx_* doesn't carry a "prompt" — use code/queries as proxy.
                        proxy = ""
                        for key in ("code", "queries", "commands", "intent"):
                            v = inp.get(key)
                            if isinstance(v, str):
                                proxy += " " + v
                            elif isinstance(v, list):
                                proxy += " " + " ".join(
                                    str(x) for x in v if isinstance(x, (str, int, float))
                                )
                        if len(proxy) > BULK_PROMPT_THRESHOLD:
                            counters["events"].append({
                                "tool": tool,
                                "prompt": proxy,
                                "on_spoke": False,
                                "surface": "ctx_mcp",
                            })
                        continue

                    if _is_spoke_mcp(tool):
                        counters["spoke_mcp"] += 1
                        # Spoke event — record for TRUST_LEAK detection.
                        # Use any string field as classifier proxy.
                        proxy_parts = [
                            str(v) for v in inp.values()
                            if isinstance(v, str)
                        ]
                        proxy = " ".join(proxy_parts)
                        counters["events"].append({
                            "tool": tool,
                            "prompt": proxy,
                            "on_spoke": True,
                            "surface": "kie_mcp",
                        })
                        continue

                    if tool == "Bash":
                        cmd = inp.get("command", "")
                        sig = _bash_signature(cmd)
                        if sig == "kie_chat":
                            counters["kie_chat"] += 1
                        elif sig == "curl_paid":
                            counters["curl_paid"] += 1
                            counters["events"].append({
                                "tool": "Bash",
                                "prompt": cmd,
                                "on_spoke": False,
                                "surface": "curl_paid",
                            })
                        elif sig == "urllib_paid":
                            counters["urllib_paid"] += 1
                            counters["events"].append({
                                "tool": "Bash",
                                "prompt": cmd,
                                "on_spoke": False,
                                "surface": "urllib_paid",
                            })
    except OSError:
        pass
    return counters


# ----- Output --------------------------------------------------------------
def ensure_section(path: str, header: str) -> None:
    """Create file if missing; append section header + description if absent."""
    text = ""
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        text = ""  # the append below will create the file
    except OSError:
        return  # unreadable for other reasons — bail without writing
    if header in text:
        return
    try:
        with open(path, "a") as f:
            f.write(
                f"\n\n{header}\n\n"
                f"_Auto-appended by `bulk-burn-tracker.py`. Each line = one "
                f"turn flagged for miscategorization (work-type ↔ surface "
                f"mismatch) per `feedback_hub_trust_spokes_economic.md`. "
                f"Format: timestamp | session | class | severity | tool | "
                f"work-type (conf) | rationale._\n\n"
            )
    except OSError:
        pass


def append_flag(line: str) -> None:
    try:
        ensure_section(LOG_FILE, SECTION_HEADER)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def format_flag(
    date: str,
    hm: str,
    session_id: str,
    miscat: str,
    severity: str,
    surface: str,
    tool: str,
    classification: dict,
) -> str:
    short_session = session_id[:8] if session_id else "unknown"
    work = classification.get("primary", "unknown")
    conf = classification.get("confidence", 0.0)
    rationale = classification.get("rationale", "")
    return (
        f"- {date} {hm} | session={short_session} | {miscat} | "
        f"severity={severity} | surface={surface} ({tool}) | "
        f"work={work} (conf={conf}) | {rationale}"
    )


# ----- Main ----------------------------------------------------------------
def compute_new_events(counters: dict, prev: dict) -> list[dict]:
    """Return only the events that occurred since the previous Stop fire.

    Strategy: events list is monotonic per session (transcript only grows).
    We track 'events_seen' count in state — anything past that index is new.
    """
    seen = prev.get("events_seen", 0)
    events = counters.get("events", [])
    return events[seen:]


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        payload = {}

    transcript_path = payload.get("transcript_path", "")
    session_id = payload.get("session_id", "") or "unknown"
    today = datetime.now().strftime("%Y-%m-%d")
    now_hm = datetime.now().strftime("%H:%M")

    all_state = load_state()
    key = f"{today}::{session_id}"
    prev = all_state.get(key, {
        "sub_total": 0, "bulk_total": 0, "hub_heavy_mcp": 0,
        "spoke_mcp": 0, "kie_chat": 0, "curl_paid": 0,
        "urllib_paid": 0, "events_seen": 0,
    })

    counters = parse_transcript(transcript_path)
    new_events = compute_new_events(counters, prev)

    # Per-turn kie_chat count — if any new spoke marker fired since last
    # turn, the turn isn't a pure hub-burn even if it also dispatched bulk.
    new_kie_chat = max(0, counters.get("kie_chat", 0) - prev.get("kie_chat", 0))

    flags_emitted = 0
    for event in new_events:
        prompt = event.get("prompt", "")
        on_spoke = event.get("on_spoke", False)
        surface = event.get("surface", "unknown")
        tool = event.get("tool", "unknown")

        classification = classify_prompt(prompt)
        model = detect_model(prompt, {})
        work_type = classification["primary"]
        severity = severity_for(model, work_type)
        miscat = miscategorization_class(
            work_type, on_spoke, classification["confidence"]
        )

        # Record EVERY classification to audit log (Phase A2 — silent on
        # low confidence; visible flag only on miscat).
        append_classifier_log({
            "ts": f"{today} {now_hm}",
            "session": session_id,
            "tool": tool,
            "surface": surface,
            "on_spoke": on_spoke,
            "classification": classification,
            "model": model,
            "severity": severity,
            "miscat": miscat,
        })

        # Telemetry surface fires only on miscat (ECONOMIC_BURN or TRUST_LEAK).
        # ECONOMIC_BURN suppressed if the SAME turn dispatched kie_chat.py —
        # operator routed correctly elsewhere; one mixed turn is not a burn.
        if miscat == "ECONOMIC_BURN" and new_kie_chat > 0:
            continue
        if miscat in ("ECONOMIC_BURN", "TRUST_LEAK"):
            append_flag(format_flag(
                today, now_hm, session_id, miscat, severity,
                surface, tool, classification,
            ))
            flags_emitted += 1

    # Persist updated counters + events_seen pointer.
    all_state[key] = {
        "sub_total": counters["sub_total"],
        "bulk_total": counters["bulk_total"],
        "hub_heavy_mcp": counters["hub_heavy_mcp"],
        "spoke_mcp": counters["spoke_mcp"],
        "kie_chat": counters["kie_chat"],
        "curl_paid": counters["curl_paid"],
        "urllib_paid": counters["urllib_paid"],
        "events_seen": len(counters.get("events", [])),
    }
    # Prune state to current month (matches prior behavior).
    cutoff = today[:7]
    all_state = {k: v for k, v in all_state.items() if k.startswith(cutoff)}
    save_state(all_state)

    sys.exit(0)


if __name__ == "__main__":
    main()
