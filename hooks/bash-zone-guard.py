#!/usr/bin/env python3
"""
Bash Zone Guard — catches write operations to Security/Zone A paths via Bash.
Complements zone-guard.py which only fires on Edit|Write tools.

S308 boil-the-ocean extension: also scans Bash command text for operator
legal-name leaks when the command appears to be a redirect-write (heredoc,
> redirect, tee, printf > file, etc.). Closes the bypass gap where an
Edit/Write would have triggered anonymity-scan.py but a Bash heredoc
slipped past untouched.

PreToolUse hook on Bash commands.
"""

import json
import os
import re
import sys

# ── Zone protection (existing logic) ──

ZONE_A_EXCEPTIONS = r"(?!hooks/|plans/|skills/|settings)"

DANGEROUS_PATTERNS = [
    rf">\s*[\"']?(?:\./)?CLAUDE\.md",
    rf">\s*[\"']?(?:\./)?product/",
    rf">\s*[\"']?(?:\./)?\.claude/{ZONE_A_EXCEPTIONS}",
    rf"\btee\b.*(?:CLAUDE\.md|product/|\.claude/{ZONE_A_EXCEPTIONS})",
    rf"\bcp\b.*\s(?:\./)?(?:CLAUDE\.md|product/|\.claude/{ZONE_A_EXCEPTIONS})",
    rf"\bmv\b.*\s(?:\./)?(?:CLAUDE\.md|product/|\.claude/{ZONE_A_EXCEPTIONS})",
    rf"\bsed\b.*-i.*(?:CLAUDE\.md|product/|\.claude/{ZONE_A_EXCEPTIONS})",
    rf"\brm\b.*(?:\./)?(?:CLAUDE\.md|product/|\.claude/{ZONE_A_EXCEPTIONS})",
]

COMPILED = [re.compile(p) for p in DANGEROUS_PATTERNS]

# ── Anonymity scan (S308 extension) ──

ANONYMITY_OVERRIDE_TOKEN = "anonymity"

# Heuristic: command is a "redirect-write" if it contains any of these patterns.
# We scan the FULL command text for legal-name leaks if it matches.
REDIRECT_WRITE_INDICATORS = [
    re.compile(r">\s*[\"']?[^\s|;&]+"),   # > file
    re.compile(r">>\s*[\"']?[^\s|;&]+"),  # >> file (append)
    re.compile(r"\bcat\s+>"),              # cat > / cat >> heredoc
    re.compile(r"\btee\b"),                # tee file
    re.compile(r"\bprintf\b.*>"),          # printf > file
    re.compile(r"<<\s*['\"]?\w+"),         # heredoc body marker
    re.compile(r"<<<\s*[\"']"),            # here-string
]

# Lazy-load anonymity_patterns from scripts/ — avoid import cost when not needed.
_anonymity_patterns = None


def _load_anonymity_patterns():
    global _anonymity_patterns
    if _anonymity_patterns is not None:
        return _anonymity_patterns
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    sys.path.insert(0, os.path.join(project_dir, "scripts"))
    try:
        import anonymity_patterns
        _anonymity_patterns = anonymity_patterns
    except ImportError:
        _anonymity_patterns = False  # mark unavailable
    return _anonymity_patterns


def is_redirect_write(command: str) -> bool:
    return any(p.search(command) for p in REDIRECT_WRITE_INDICATORS)


def deny(reason: str) -> None:
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))
    sys.exit(0)


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    # ── Zone protection (highest priority) ──
    for pattern in COMPILED:
        match = pattern.search(command)
        if match:
            matched_text = match.group(0)
            deny(
                f"BASH ZONE GUARD: Command targets a protected path "
                f"(Security or Zone A). Matched: '{matched_text}'. "
                f"Use Edit/Write tools instead, or get explicit operator approval."
            )

    # ── Anonymity scan on redirect-writes (S308 extension) ──
    if os.environ.get("SENTINEL_OVERRIDE") == ANONYMITY_OVERRIDE_TOKEN:
        sys.exit(0)

    if is_redirect_write(command):
        ap = _load_anonymity_patterns()
        if ap and ap is not False:
            # Skip if the redirect target is a known tooling file (heuristic:
            # extract the path after > / >> / tee and check the whitelist)
            target_match = re.search(r"(?:>>?|\btee\b\s+)[\"']?([^\s|;&'\"]+)", command)
            if target_match and ap.is_tooling_file(target_match.group(1)):
                sys.exit(0)

            hits = ap.count_leaks(command)
            if hits:
                summary = ", ".join(f"{label}×{count}" for label, count in hits)
                total = sum(c for _, c in hits)
                deny(
                    f"[ANONYMITY-SCAN via bash-zone-guard] {total} legal-name leak(s) "
                    f"detected in Bash redirect-write command:\n"
                    f"  - {summary}\n"
                    f"This Bash command appears to write content containing operator "
                    f"legal-name forms. Edit/Write tools fire anonymity-scan.py, but "
                    f"Bash heredoc/redirect bypasses that hook — this check closes the "
                    f"gap.\n"
                    f"Rule: forward-mitigation only per "
                    f"memory/feedback_anonymity_mitigation_not_fix.md (S308) + "
                    f"memory/feedback_operator_anonymity.md (S262).\n"
                    f"Substitute legal-name forms with 'the operator' / SEC persona / "
                    f"brand handle as context-appropriate.\n"
                    f"Override (this turn): SENTINEL_OVERRIDE=anonymity"
                )

    sys.exit(0)


if __name__ == "__main__":
    main()
