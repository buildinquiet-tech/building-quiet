#!/usr/bin/env python3
"""Capitulation Sentinel — Stop hook.

Detects agreeable-assistant capitulation patterns in the assistant's last
turn and forces continuation if no evidence (tool use, audit table, file
references) accompanies the agreement.

Enforces feedback_kernel_sparring_default.md (S281) — Echo defaults to
sparring partner, not yes-machine. Stanford-glazing fix.

Trigger: Stop hook fires after assistant emits a stop signal.
Behavior:
  - Read transcript_path from hook input
  - Extract last assistant turn
  - Detect capitulation phrase
  - Check for accompanying evidence in same turn
  - If capitulation alone → force continuation with sparring reminder
  - If capitulation + evidence → pass

Override: SENTINEL_OVERRIDE=capitulation
"""

import json
import sys
import os
import re

CAPITULATION_PATTERNS = [
    re.compile(r"\byou'?re (absolutely |totally |completely )?right\b", re.IGNORECASE),
    re.compile(r"\bi should have\b|\bi should'?ve\b", re.IGNORECASE),
    re.compile(r"\bmy apolog(y|ies)\b", re.IGNORECASE),
    re.compile(r"\bgood (catch|point)\b", re.IGNORECASE),
    re.compile(r"\bfair point\b", re.IGNORECASE),
    re.compile(r"\bi made a mistake\b", re.IGNORECASE),
]

EVIDENCE_INDICATORS = [
    r"\|\s*-+\s*\|",        # markdown table separator
    r"\|.*\|.*\|",           # markdown table row
    r"```",                   # code block
    r"^\s*\d+\.\s+",         # numbered list
    r"^\s*[-*]\s+\*\*",      # bullet with bold (audit-style)
    r"\.md[:#]",              # file reference with line/anchor
    r"line\s+\d+",            # line citation
    r"feedback_\w+\.md",     # feedback memory citation
    r"reference_\w+\.md",    # reference citation
    r"S\d{3}\b",              # session citation
    r"D-\d{4}-\d{4}",         # decision ID
    r"LAW \d+",               # LAW citation
    r"BQ-\d",           # content ID
]

EVIDENCE_RES = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in EVIDENCE_INDICATORS]


def extract_last_assistant_text(transcript_path):
    """Read JSONL transcript, return concatenated text of last assistant turn."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return ""

    # Walk backwards to last assistant message
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
                if isinstance(blk, dict):
                    if blk.get("type") == "text":
                        parts.append(blk.get("text", ""))
            return "\n".join(parts)
        return ""
    return ""


def has_capitulation(text):
    for pat in CAPITULATION_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def has_evidence(text):
    if not text:
        return False
    hits = 0
    for pat in EVIDENCE_RES:
        if pat.search(text):
            hits += 1
            if hits >= 2:
                return True
    return False


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "capitulation":
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)

    transcript_path = data.get("transcript_path", "")
    text = extract_last_assistant_text(transcript_path)
    if not text:
        sys.exit(0)

    phrase = has_capitulation(text)
    if not phrase:
        sys.exit(0)

    if has_evidence(text):
        sys.exit(0)

    msg = (
        f"[SPARRING CHECK] Capitulation phrase detected: \"{phrase}\".\n"
        f"Per feedback_kernel_sparring_default.md (S281): Echo defaults to "
        f"sparring partner, not agreeing assistant. The response agreed with "
        f"the operator's flag without producing audit evidence in the same "
        f"turn (no tables, file citations, session refs, or LAW citations).\n\n"
        f"Re-engage: either (a) audit whether the flag is fully accurate and "
        f"surface the actual analysis, or (b) acknowledge agreement WITH "
        f"evidence — file paths, line numbers, feedback memory citations, "
        f"or a structured audit table.\n\n"
        f"Override (this turn): SENTINEL_OVERRIDE=capitulation"
    )

    output = {
        "decision": "block",
        "reason": msg,
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
