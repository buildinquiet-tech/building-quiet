#!/usr/bin/env python3
"""
Prompt Sentinel v2 — PreToolUse hook for UNIVERSAL prompt quality enforcement.
Scores prompts against 3+5 "Great Prompting" checklist.
Targets: ALL generative tools (kie.ai, ElevenLabs, Canva, Firecrawl agents, Agent dispatches).

3 CORE fields (required): What (task+context), Why (purpose+tone), Way (anti-patterns+output)
5 OPTIONAL fields: Background data, Examples, Detailed rules, Format spec, Reference files

Scoring:
  0 core missing   → PASS (log only)
  1 core missing   → FLAG (warning, proceed)
  2+ core missing  → BLOCK (exit 2, must fix before proceeding)

Reports: Every scored prompt logged to echo/prompt-sentinel-report.json

Pass-file: /great-prompt writes /tmp/.prompt-sentinel-pass-{piece_id}.
If pass-file exists and <2hr old → auto-PASS (skip scoring entirely).

S171: Upgraded from advisory-only to blocking. Universal scope per operator directive.
"""

import json
import os
import re
import sys
import time

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(os.path.dirname(HOOKS_DIR))
PROMPT_LOG = os.path.join(REPO_DIR, "echo", "prompt-log.md")
REPORT_FILE = os.path.join(REPO_DIR, "echo", "prompt-sentinel-report.json")
PASS_FILE_PREFIX = "/tmp/.prompt-sentinel-pass-"
PASS_FILE_MAX_AGE = 7200  # 2 hours in seconds

# Tools this hook targets — ALL generative/creative dispatches
KIE_AI_PREFIX = "mcp__kie-ai__"
ELEVENLABS_PREFIX = "mcp__elevenlabs__"
CANVA_PREFIX = "mcp__canva__"
FIRECRAWL_AGENT = "mcp__firecrawl__firecrawl_agent"
AGENT_TOOL = "Agent"

# Tools to SKIP (non-generative operations)
SKIP_TOOLS = {
    "mcp__kie-ai__get_task_status",
    "mcp__kie-ai__list_tasks",
    "mcp__elevenlabs__check_subscription",
    "mcp__elevenlabs__list_models",
    "mcp__elevenlabs__list_agents",
    "mcp__elevenlabs__get_voice",
    "mcp__elevenlabs__search_voices",
    "mcp__elevenlabs__list_phone_numbers",
    "mcp__canva__authenticate",
}

# 3 Core fields (required)
CORE_FIELDS = ["What", "Why", "Way"]
# 5 Optional fields (improve score but never block)
OPTIONAL_FIELDS = [
    "Background data",
    "Examples",
    "Detailed rules",
    "Format spec",
    "Reference files",
]
ALL_FIELDS = CORE_FIELDS + OPTIONAL_FIELDS

# ── Heuristic patterns ──

PIECE_ID_RE = re.compile(r"(?:OZ|BIQ)-[A-Z]\d+", re.IGNORECASE)
BRAND_RE = re.compile(r"@(?:@yourbrand1|yourbrand2|@yourbrand3)", re.IGNORECASE)
BEAT_RE = re.compile(r"[Bb]eat\s+\d+")
MOOD_WORDS = re.compile(
    r"\b(?:cinematic|dark|moody|warm|raw|minimal|dramatic|atmospheric|"
    r"contemplative|gritty|soft|harsh|clinical|ambient|tense|eerie|"
    r"playful|somber|upbeat|melancholic|energetic|calm|intense)\b",
    re.IGNORECASE,
)
DIMENSION_RE = re.compile(r"\b(?:720|1080|1280|1296|9:16|16:9|4:3|1:1)\b")
DURATION_RE = re.compile(r"\b\d+s\b|\b\d+\s*(?:seconds?|sec)\b", re.IGNORECASE)
MODEL_RE = re.compile(
    r"\b(?:veo3|veo|kling|runway|nano.banana|seedance|hailuo|wan|sora)\b",
    re.IGNORECASE,
)
COST_RE = re.compile(r"\$\d+(?:\.\d+)?")
FILE_REF_RE = re.compile(r"\S+\.(?:mp4|mp3|png|jpg|jpeg|wav|webm)", re.IGNORECASE)
SESSION_RE = re.compile(r"\bS\d{2,3}\b")
RELATIVE_REF_RE = re.compile(
    r"\b(?:similar to|like the|reference|based on|same as|like last)\b",
    re.IGNORECASE,
)
NEGATION_RE = re.compile(
    r"\b(?:NOT|never|avoid|don'?t|do not|no\s+\w+|without)\b",
    re.IGNORECASE,
)
ACTION_RE = re.compile(
    r"\b(?:generate|create|produce|build|make|render|synthesize|compose)\b",
    re.IGNORECASE,
)
FORMAT_RE = re.compile(
    r"\b(?:mp4|mp3|png|jpg|h264|h265|wav|webm|gif)\b|\b\d+x\d+\b|\b\d+p\b",
    re.IGNORECASE,
)


def check_pass_file():
    """Check if /great-prompt wrote a recent pass-file. Returns True if valid pass exists."""
    import glob as _glob
    pass_files = _glob.glob(PASS_FILE_PREFIX + "*")
    now = time.time()
    for pf in pass_files:
        try:
            age = now - os.path.getmtime(pf)
            if age < PASS_FILE_MAX_AGE:
                return True
            else:
                os.remove(pf)  # Clean up expired
        except OSError:
            continue
    return False


def score_prompt(text):
    """Score a prompt against 3 core + 5 optional fields.
    Returns (core_missing, optional_missing, missing_core_names, missing_optional_names).
    """
    if not text:
        return 3, 5, list(CORE_FIELDS), list(OPTIONAL_FIELDS)

    missing_core = []
    missing_optional = []

    # CORE 1 — What (task + context): piece ID, brand, beat, or action verb + >50 chars
    has_what = (
        PIECE_ID_RE.search(text)
        or BRAND_RE.search(text)
        or BEAT_RE.search(text)
        or (ACTION_RE.search(text) and len(text.strip()) > 50)
    )
    if not has_what:
        missing_core.append("What")

    # CORE 2 — Why (purpose + tone): mood words or explicit purpose language
    has_why = MOOD_WORDS.search(text) or len(text.strip()) > 100
    if not has_why:
        missing_core.append("Why")

    # CORE 3 — Way (anti-patterns + expected output): negation OR format spec
    has_way = NEGATION_RE.search(text) or FORMAT_RE.search(text)
    if not has_way:
        missing_core.append("Way")

    # OPTIONAL 1 — Background data: dimensions, duration, model, cost
    bg_signals = sum([
        bool(DIMENSION_RE.search(text)),
        bool(DURATION_RE.search(text)),
        bool(MODEL_RE.search(text)),
        bool(COST_RE.search(text)),
    ])
    if bg_signals < 1:
        missing_optional.append("Background data")

    # OPTIONAL 2 — Examples: file path, session ref, relative ref
    if not (FILE_REF_RE.search(text) or SESSION_RE.search(text) or
            RELATIVE_REF_RE.search(text)):
        missing_optional.append("Examples")

    # OPTIONAL 3 — Detailed rules: prompt length > 150 chars
    if len(text.strip()) <= 150:
        missing_optional.append("Detailed rules")

    # OPTIONAL 4 — Format spec: explicit format mention
    if not FORMAT_RE.search(text):
        missing_optional.append("Format spec")

    # OPTIONAL 5 — Reference files: file paths
    if not FILE_REF_RE.search(text):
        missing_optional.append("Reference files")

    return len(missing_core), len(missing_optional), missing_core, missing_optional


def extract_prompt_text(tool_name, tool_input):
    """Extract the prompt text from tool input based on tool type."""
    if tool_name == AGENT_TOOL:
        return tool_input.get("prompt", "")
    # kie.ai MCP tools — check common parameter names
    for key in ("prompt", "text", "input", "description", "script"):
        if key in tool_input:
            return str(tool_input[key])
    # Fallback: concatenate all string values
    parts = []
    for v in tool_input.values():
        if isinstance(v, str) and len(v) > 10:
            parts.append(v)
    return " ".join(parts)


def log_report(tool_name, score, result, core_names, opt_names, prompt_preview):
    """Log every scored prompt to the report file."""
    import datetime
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "tool": tool_name,
        "score": f"{score}/8",
        "result": result,
        "core_missing": core_names,
        "optional_missing": opt_names,
        "prompt_preview": prompt_preview[:120],
    }
    try:
        if os.path.exists(REPORT_FILE):
            with open(REPORT_FILE, "r") as f:
                data = json.load(f)
        else:
            data = []
        data.append(entry)
        # Keep last 200 entries
        if len(data) > 200:
            data = data[-200:]
        with open(REPORT_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass  # Never crash on logging failure


def main():
    try:
        event = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, Exception):
        sys.exit(0)

    tool_name = event.get("tool_name", "")

    # Skip non-generative tools explicitly
    if tool_name in SKIP_TOOLS:
        sys.exit(0)

    # Fire on ALL generative tools
    is_kie_ai = tool_name.startswith(KIE_AI_PREFIX)
    is_elevenlabs = tool_name.startswith(ELEVENLABS_PREFIX)
    is_canva = tool_name.startswith(CANVA_PREFIX)
    is_firecrawl_agent = tool_name == FIRECRAWL_AGENT
    is_agent = tool_name == AGENT_TOOL

    if not (is_kie_ai or is_elevenlabs or is_canva or is_firecrawl_agent or is_agent):
        sys.exit(0)

    # Check for pass-file from /great-prompt — auto-PASS if found
    if check_pass_file():
        sys.exit(0)

    tool_input = event.get("tool_input", {})
    prompt_text = extract_prompt_text(tool_name, tool_input)

    # Skip very short agent dispatches (system tasks, not creative prompts)
    if is_agent and len(prompt_text) < 30:
        sys.exit(0)

    core_missing, opt_missing, core_names, opt_names = score_prompt(prompt_text)
    total_score = (3 - core_missing) + (5 - opt_missing)

    if core_missing == 0:
        # All cores present — PASS
        log_report(tool_name, total_score, "PASS", core_names, opt_names, prompt_text)
        if opt_missing > 0:
            opt_str = ", ".join(opt_names)
            print(
                f"PROMPT SENTINEL PASS: {total_score}/8 — Optional missing: {opt_str}.",
                file=sys.stderr,
            )
        sys.exit(0)
    elif core_missing == 1:
        # 1 core missing — FLAG (warning, proceed)
        core_str = ", ".join(core_names)
        log_report(tool_name, total_score, "FLAG", core_names, opt_names, prompt_text)
        print(
            f"PROMPT SENTINEL FLAG: {total_score}/8 — Core missing: {core_str}. Consider /great-prompt.",
            file=sys.stderr,
        )
        sys.exit(0)
    else:
        # 2-3 cores missing — BLOCK (must fix)
        core_str = ", ".join(core_names)
        log_report(tool_name, total_score, "BLOCK", core_names, opt_names, prompt_text)
        print(
            json.dumps({
                "result": "block",
                "reason": f"PROMPT SENTINEL BLOCK: {total_score}/8 — Missing cores: {core_str}. Run /great-prompt or add What/Why/Way before dispatching.",
            })
        )
        sys.exit(2)  # BLOCK — teeth


if __name__ == "__main__":
    main()
