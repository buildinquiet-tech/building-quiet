#!/usr/bin/env python3
"""Time-Estimate Sentinel — Stop hook.

Detects time-quote patterns in the assistant's last turn and flags inflated
or unrationalized estimates per feedback_time_estimates_use_ranges.md (S307).

Aligned with docs/_active/task-model-routing.md — rationale tokens map to
known spokes (hub-only / Gemini Deep Research / kie.ai / Ollama / blocked-on).
The same hook polices LAW 1 (Hub & Spoke routing discipline) since every
estimate is implicitly a routing claim.

Trigger: Stop hook fires after assistant emits a stop signal.
Behavior:
  - Read transcript_path, extract last assistant turn
  - Detect time-quote patterns inside completion-context sentences
  - Flag if: single number (no range), >3x range without "scope unclear"
    carve-out, or range without spoke-anchored rationale
  - Always log captured estimates to echo/time-estimate-log.jsonl for
    calibration loop (compared to actual elapsed time at /closing Phase 2f)

Sister hook to: capitulation-sentinel.py (S281), bulk-burn-tracker.py (LAW 1).
Override: SENTINEL_OVERRIDE=time-estimate
"""

import json
import sys
import os
import re
from datetime import datetime
from pathlib import Path


COMPLETION_CONTEXTS = [
    re.compile(r"\b(takes?|take|took)\b", re.IGNORECASE),
    re.compile(r"\bwill take\b", re.IGNORECASE),
    re.compile(r"\bestimate[ds]?\b", re.IGNORECASE),
    re.compile(r"\bETA\b", re.IGNORECASE),
    re.compile(r"\b(approx|approximately|roughly|about)\b", re.IGNORECASE),
    re.compile(r"\bfinish(?:ed)?\s+in\b", re.IGNORECASE),
    re.compile(r"\bdone\s+in\b", re.IGNORECASE),
    re.compile(r"\bwrap\s+in\b", re.IGNORECASE),
    re.compile(r"\bto\s+ship\b", re.IGNORECASE),
    re.compile(r"~\s*\d+\s*(min|minute|hr|hour|day)", re.IGNORECASE),
    re.compile(r"\bbuild\s+(estimate|time|order)\b", re.IGNORECASE),
    re.compile(r"\bsession\s+elapsed\b", re.IGNORECASE),
]


DESCRIPTIVE_CONTEXTS = [
    re.compile(r"\bago\b", re.IGNORECASE),
    re.compile(r"\belapsed\s+since\b", re.IGNORECASE),
    re.compile(r"\bdays?\s+back\b", re.IGNORECASE),
    re.compile(r"\bremaining\b", re.IGNORECASE),
    re.compile(r"\bresets?\s+in\b", re.IGNORECASE),
    re.compile(r"\buntil\s+(?:reset|launch|deadline|the)\b", re.IGNORECASE),
    re.compile(r"\bfrom\s+now\b", re.IGNORECASE),
    re.compile(r"\bcountdown\b", re.IGNORECASE),
]

SYSTEM_REMINDER_BLOCK_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>", re.DOTALL | re.IGNORECASE
)

SENTINEL_FEEDBACK_BLOCK_RE = re.compile(
    r"\[TIME-ESTIMATE CHECK\].*?Override \(this turn\):\s*SENTINEL_OVERRIDE=time-estimate",
    re.DOTALL | re.IGNORECASE
)


TIME_QUOTE_RE = re.compile(
    r"(?<![\$\.\-/v])"
    r"\b(\d+)(?:\s*[-–—]\s*(\d+))?"
    r"\s*(min|mins|minute|minutes|hr|hrs|hour|hours|day|days)\b",
    re.IGNORECASE,
)


RATIONALE_TOKENS = re.compile(
    r"\b("
    r"hub[- ]only|hub claude|max sub|"
    r"spoke|fan[- ]out|parallel|"
    r"gemini deep research|gemini api|"
    r"kie\.?ai|kling|veo|nano banana|elevenlabs|"
    r"ollama|local|sed|grep|tier 6|"
    r"render|render queue|render-queue|"
    r"blocked on|waiting on|pending|"
    r"discover card|api rate|sla|"
    r"scope unclear|honest unknown|requoting|"
    r"file edits?|file moves?|commit|"
    r"three file|two file|four file|five file|N file|"
    r"propose first|zone a"
    r")\b",
    re.IGNORECASE,
)


HONEST_UNKNOWN = re.compile(
    r"\b(scope unclear|honest unknown|requote|requoting|"
    r"don'?t know|unknown floor|depends on what|after .* discovery)\b",
    re.IGNORECASE,
)


def extract_last_assistant_text(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return ""

    for line in reversed(lines):
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
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    parts.append(blk.get("text", ""))
            return "\n".join(parts)
        return ""
    return ""


def near(text, idx, window=120):
    start = max(0, idx - window)
    end = min(len(text), idx + window)
    return text[start:end]


def detect_estimates(text):
    if not text:
        return
    no_reminder = SYSTEM_REMINDER_BLOCK_RE.sub(" ", text)
    no_feedback = SENTINEL_FEEDBACK_BLOCK_RE.sub(" ", no_reminder)
    no_code = re.sub(r"```.*?```", " ", no_feedback, flags=re.DOTALL)
    no_inline = re.sub(r"`[^`]+`", " ", no_code)

    for m in TIME_QUOTE_RE.finditer(no_inline):
        ctx = near(no_inline, m.start(), window=120)
        if any(d.search(ctx) for d in DESCRIPTIVE_CONTEXTS):
            continue
        if not any(c.search(ctx) for c in COMPLETION_CONTEXTS):
            continue
        low = int(m.group(1))
        high = int(m.group(2)) if m.group(2) else None
        unit = m.group(3).lower()
        yield (m.group(0), low, high, unit, ctx)


def check_estimate(low, high, ctx):
    has_rationale = bool(RATIONALE_TOKENS.search(ctx))
    has_unknown = bool(HONEST_UNKNOWN.search(ctx))

    if high is None:
        if has_unknown:
            return ("pass", "")
        return ("single", f"single-number quote ({low}); rule requires bounded range")

    width_ratio = high / low if low > 0 else 999
    if width_ratio > 3 and not has_unknown:
        return ("wide",
                f"range too wide ({low}-{high}, ratio {width_ratio:.1f}x); "
                f"tighten or add 'scope unclear' carve-out")

    if not has_rationale and not has_unknown:
        return ("no_rationale",
                f"range {low}-{high} has no rationale token; "
                f"add hub-only/spoke fan-out/Gemini Deep Research/blocked-on")

    return ("pass", "")


def log_estimate(project_dir, est, verdict, reason):
    log_dir = Path(project_dir) / "echo"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    log_path = log_dir / "time-estimate-log.jsonl"

    full, low, high, unit, ctx = est
    entry = {
        "ts": datetime.now().isoformat(),
        "quote": full,
        "low": low,
        "high": high,
        "unit": unit,
        "verdict": verdict,
        "reason": reason,
        "ctx_snippet": ctx[:200],
    }
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "time-estimate":
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    transcript_path = data.get("transcript_path", "")
    text = extract_last_assistant_text(transcript_path)
    if not text:
        sys.exit(0)

    estimates = list(detect_estimates(text))
    if not estimates:
        sys.exit(0)

    violations = []
    for est in estimates:
        full, low, high, unit, ctx = est
        verdict, reason = check_estimate(low, high, ctx)
        log_estimate(project_dir, est, verdict, reason)
        if verdict != "pass":
            violations.append((full, verdict, reason))

    if not violations:
        sys.exit(0)

    bullets = "\n".join(f"  - \"{q}\" — {r}" for q, _, r in violations)
    msg = (
        f"[TIME-ESTIMATE CHECK] {len(violations)} time-quote violation(s) "
        f"per feedback_time_estimates_use_ranges.md (S307):\n"
        f"{bullets}\n\n"
        f"Rule: bounded ranges (X-Y min/hr) + one-line rationale anchored on "
        f"task-model-routing.md spokes (hub-only / spoke fan-out / "
        f"Gemini Deep Research / kie.ai / blocked-on). Echo-hours, not "
        f"human-hours.\n\n"
        f"Re-engage: requote with tightened range + rationale, or add "
        f"'scope unclear' carve-out for honest unknowns.\n\n"
        f"Override (this turn): SENTINEL_OVERRIDE=time-estimate"
    )

    output = {"decision": "block", "reason": msg}
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
