#!/usr/bin/env python3
"""
Quality Gate Hook — Quiet Method Content Validator
Runs as a PreToolUse hook before Metricool REST publishing.
Validates content against brand rules and blocks if violations found.
Logs all violations to violation-log.json for pattern analysis by /reflect.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

# ── Paths ──

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VIOLATION_LOG = os.path.join(SCRIPT_DIR, "violation-log.json")

# ── Banned Word Lists (from CLAUDE.md Language Rules) ──

BANNED_HYPE_WORDS = [
    "unlimited", "infinite", "secret", "hidden", "revolutionary",
    "game-changing", "instant", "overnight", "guaranteed", "proven",
    "surefire", "massive", "huge", "enormous", "don't miss out",
    "act now", "crush it", "kill it", "dominate", "forever",
]

BANNED_AI_FILLER = [
    "delve", "embark", "enlightening", "esteemed", "shed light",
    "craft", "crafting", "imagine", "realm", "unlock", "discover",
    "skyrocket", "abyss", "not alone", "in a world where",
    "revolutionize", "disruptive", "utilize", "utilizing", "dive deep",
    "tapestry", "illuminate", "unveil", "pivotal", "intricate",
    "elucidate", "hence", "furthermore", "however", "harness",
    "exciting", "groundbreaking", "cutting-edge", "remarkable",
    "remains to be seen", "glimpse into", "navigating", "landscape",
    "stark", "testament", "in summary", "in conclusion", "moreover",
    "boost", "powerful", "inquiries", "ever-evolving",
    "certainly", "probably", "basically", "literally",
]

BANNED_INCOME_PHRASES = [
    "made money", "sales notifications", "most profitable",
    "builds wealth", "build wealth", "building wealth",
]

BANNED_BRAND_WORDS = [
    "empire",
]

BANNED_CTA_PHRASES = [
    "link in bio",
    "click here",
    "check it out",
    "don't miss this",
]

BANNED_VISUAL_TERMS = [
    "matte black", "classified", "night black",
    "#0b0b0b", "#d4a632",
]


def check_banned_words(text):
    """Check for banned hype words. Returns list of found violations."""
    violations = []
    text_lower = text.lower()
    for word in BANNED_HYPE_WORDS:
        pattern = r"\b" + re.escape(word) + r"\b"
        if re.search(pattern, text_lower):
            violations.append({"category": "hype_word", "detail": word})
    return violations


def check_ai_filler(text):
    """Check for banned AI filler words/phrases. Returns list of found violations."""
    violations = []
    text_lower = text.lower()
    for phrase in BANNED_AI_FILLER:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, text_lower):
            violations.append({"category": "ai_filler", "detail": phrase})
    return violations


DASH_VARIANTS = {
    "\u2014": "em dash (—, U+2014)",
    "\u2013": "en dash (–, U+2013)",
    "\u2012": "figure dash (‒, U+2012)",
    "\u2E3A": "two-em dash (⸺, U+2E3A)",
    "\u2E3B": "three-em dash (⸻, U+2E3B)",
    "\uFF0D": "fullwidth hyphen-minus (－, U+FF0D)",
}


def check_em_dashes(text):
    """Check for em dashes. Returns list of found violations."""
    violations = []
    for char, label in DASH_VARIANTS.items():
        if char in text:
            count = text.count(char)
            violations.append({"category": "em_dash", "detail": f"{count}x {label}"})
    return violations
    return []


def check_ftc_compliance(text):
    """Check FTC disclosure for affiliate content. Returns list of found violations.

    Updated 2026-03-19: Removed #ad from accepted patterns (deprecated S76).
    Use "Paid Partnership" tag (platform-level) + "I earn a commission" (caption).
    """
    violations = []
    text_lower = text.lower()
    mentions_affiliate = any(
        kw in text_lower for kw in ["affiliate", "commission"]
    )
    if mentions_affiliate:
        # Accept natural disclosure patterns (NOT #ad — deprecated S76)
        disclosure_patterns = [
            "#affiliate",
            "i earn a commission",
            "affiliate link",
            "paid partnership",
            "i may earn",
            "earn a commission",
        ]
        has_disclosure = any(p in text_lower for p in disclosure_patterns)
        if not has_disclosure:
            violations.append({
                "category": "ftc",
                "detail": "Missing affiliate disclosure (add 'I earn a commission' or 'Paid partnership' near CTA)"
            })
    # Warn if #ad is used (deprecated — suppresses reach per S76 data)
    if "#ad" in text_lower:
        violations.append({
            "category": "ftc",
            "detail": "#ad is deprecated (S76) — use 'Paid partnership' tag + 'I earn a commission' instead"
        })
    return violations


def check_income_claims(text):
    """Check for banned income claim phrases. Returns list of found violations."""
    violations = []
    text_lower = text.lower()
    for phrase in BANNED_INCOME_PHRASES:
        if phrase in text_lower:
            violations.append({"category": "income_claim", "detail": phrase})

    sleep_count = len(re.findall(r"\bwhile i sleep\b", text_lower))
    if sleep_count > 1:
        violations.append({
            "category": "income_claim",
            "detail": f"\"while i sleep\" used {sleep_count}x (max 1)"
        })
    return violations


def check_brand_words(text):
    """Check for banned brand words in public content. Returns list of found violations."""
    violations = []
    text_lower = text.lower()
    for word in BANNED_BRAND_WORDS:
        pattern = r"\b" + re.escape(word) + r"\b"
        if re.search(pattern, text_lower):
            violations.append({"category": "brand_word", "detail": word})
    return violations


def check_visual_terms(text):
    """Check for old QE visual system terms in @yourbrand1 content."""
    violations = []
    text_lower = text.lower()
    for term in BANNED_VISUAL_TERMS:
        if term.lower() in text_lower:
            violations.append({"category": "visual_identity", "detail": term})
    return violations


def check_banned_ctas(text):
    """Check for banned CTA phrases. Returns list of found violations."""
    violations = []
    text_lower = text.lower()
    for phrase in BANNED_CTA_PHRASES:
        if phrase in text_lower:
            violations.append({"category": "banned_cta", "detail": phrase})
    return violations


def check_hashtags_in_body(text):
    """Check for hashtags in body copy (no inline hashtags allowed)."""
    violations = []
    lines = text.strip().split("\n")
    # Hashtags only allowed in a trailing hashtag block (all-hashtag lines)
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        # If line is ALL hashtags, it's the hashtag block — allowed
        words = line_stripped.split()
        if all(w.startswith("#") for w in words):
            continue
        # Check for hashtags mixed into body copy
        body_hashtags = re.findall(r"#(?!ad\b)\w+", line_stripped)
        if body_hashtags:
            violations.append({
                "category": "hashtag_in_body",
                "detail": f"{', '.join(body_hashtags[:3])} in body copy (move to end)"
            })
            break  # one violation is enough
    return violations


def check_dollar_amounts(text):
    """Check for specific dollar amounts that imply income claims.

    Context-aware: skips dollar amounts in comparison/cost contexts
    (e.g. tool pricing, stack cost breakdowns) where the figures
    describe expenses, not earnings. Option C — approved 2026-04-10.
    """
    violations = []

    # Comparison-context keywords — if ANY appear near dollar amounts,
    # these are cost/expense figures, not income claims.
    comparison_markers = [
        r"old way", r"new way", r"current stack", r"replaces",
        r"instead of", r"vs\.?", r"compared to", r"before",
        r"used to pay", r"were paying", r"costs?", r"pricing",
        r"subscription", r"per month", r"/mo\b", r"/yr\b",
        r"free tier", r"stack cost",
    ]
    comparison_pattern = "|".join(comparison_markers)
    in_comparison_context = bool(re.search(comparison_pattern, text, re.IGNORECASE))

    # Match $XX, $XXX, $X,XXX, $XX,XXX patterns (specific earnings)
    dollar_matches = re.findall(r"\$[\d,]+(?:\.\d{2})?(?:\s*/\s*(?:month|mo|week|wk|day|hr|hour|year|yr))?", text)
    if dollar_matches and not in_comparison_context:
        for match in dollar_matches[:3]:
            violations.append({
                "category": "income_claim",
                "detail": f"Dollar amount '{match}' — use relative language instead"
            })
    return violations


def check_not_just_but(text):
    """Check for 'not just X, but Y' constructions (content-voice.md Rule 6)."""
    violations = []
    if re.search(r"\bnot just\b.{1,60}\bbut\b", text, re.IGNORECASE):
        violations.append({
            "category": "style",
            "detail": "\"not just X, but Y\" — rewrite as direct statement"
        })
    return violations


def check_youtube_affiliate_title(title, text):
    """Check YouTube affiliate titles include 'ad' or 'sponsored'."""
    violations = []
    if not title:
        return violations
    text_lower = (text or "").lower()
    mentions_affiliate = any(
        kw in text_lower for kw in ["affiliate", "commission"]
    )
    if mentions_affiliate:
        title_lower = title.lower()
        if "ad" not in title_lower and "sponsored" not in title_lower:
            violations.append({
                "category": "ftc",
                "detail": "YouTube title missing 'ad' or 'sponsored' for affiliate content"
            })
    return violations


def check_ai_disclosure(text):
    """Check for AI-generated content disclosure (2025 FTC requirement)."""
    violations = []
    text_lower = text.lower()
    disclosure_patterns = [
        "ai-generated", "ai generated", "created with ai", "made with ai",
        "ai-created", "#aigenerated", "#ai_generated",
    ]
    if not any(p in text_lower for p in disclosure_patterns):
        violations.append({
            "category": "ftc",
            "detail": "Missing AI-generated content disclosure — add #AIgenerated to hashtags (2025 FTC requirement)"
        })
    return violations


def check_formatting(text):
    """Check for formatting violations. Returns list of found violations."""
    violations = []
    if ";" in text:
        violations.append({"category": "formatting", "detail": "semicolon"})
    if re.search(r"\*[^*\n]+\*", text):
        violations.append({"category": "formatting", "detail": "markdown asterisks"})
    return violations


def validate(text):
    """Run all validation checks. Returns list of all violations."""
    violations = []
    violations.extend(check_banned_words(text))
    violations.extend(check_ai_filler(text))
    violations.extend(check_em_dashes(text))
    violations.extend(check_ftc_compliance(text))
    violations.extend(check_income_claims(text))
    violations.extend(check_brand_words(text))
    violations.extend(check_visual_terms(text))
    violations.extend(check_banned_ctas(text))
    violations.extend(check_hashtags_in_body(text))
    violations.extend(check_dollar_amounts(text))
    violations.extend(check_not_just_but(text))
    violations.extend(check_ai_disclosure(text))
    violations.extend(check_formatting(text))
    return violations


def log_violations(violations, content_snippet):
    """Append violations to the violation log for /reflect analysis."""
    try:
        if os.path.exists(VIOLATION_LOG):
            with open(VIOLATION_LOG, "r") as f:
                log = json.load(f)
        else:
            log = []
    except (json.JSONDecodeError, IOError):
        log = []

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "violations": violations,
        "content_preview": content_snippet[:120] + "..." if len(content_snippet) > 120 else content_snippet,
        "violation_count": len(violations),
    }
    log.append(entry)

    try:
        with open(VIOLATION_LOG, "w") as f:
            json.dump(log, f, indent=2)
    except IOError:
        pass


def format_violation_message(violation):
    """Format a violation dict into a human-readable string."""
    category = violation["category"]
    detail = violation["detail"]
    labels = {
        "hype_word": "Banned hype word",
        "ai_filler": "AI filler word",
        "em_dash": "Em dash",
        "ftc": "FTC violation",
        "income_claim": "Income claim",
        "brand_word": "Banned brand word",
        "formatting": "Formatting",
        "visual_identity": "Old QE visual term",
        "banned_cta": "Banned CTA phrase",
        "hashtag_in_body": "Hashtag in body copy",
        "style": "Style violation",
        "ai_disclosure": "AI disclosure",
    }
    label = labels.get(category, category)
    return f"{label}: \"{detail}\""


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    text = tool_input.get("text", "")
    title = tool_input.get("title", "")

    # Also validate thread content (additionalPosts for X/Threads threading)
    additional_posts = tool_input.get("additionalPosts", [])
    thread_texts = [p.get("text", "") for p in additional_posts if isinstance(p, dict)]
    content = "\n".join([text, title] + thread_texts).strip()

    if not content:
        sys.exit(0)

    violations = validate(content)

    # Platform-specific checks
    platform = tool_input.get("platform", "")
    if platform == "youtube":
        violations.extend(check_youtube_affiliate_title(title, text))

    # Always log — even passes (empty violations list)
    log_violations(violations, content)

    if violations:
        reason = "QUALITY GATE BLOCKED — Fix before publishing:\n"
        for v in violations:
            reason += f"  - {format_violation_message(v)}\n"
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
        print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
