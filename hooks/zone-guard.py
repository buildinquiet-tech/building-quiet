#!/usr/bin/env python3
"""
Zone Guard Hook — Execution Zone Permission Enforcer
Runs as a PreToolUse hook before Edit|Write operations.
Enforces Security/A/B/C permissions per CLAUDE.md Execution Zones.

Hierarchy (most → least restricted):
  Security  →  HARD BLOCK  (CLAUDE.md, product/)
  Zone A    →  BLOCK       (.claude/ with exceptions)
  Zone B    →  WARN        (docs/ except _active/, echo/VISION.md)
  Zone C    →  AUTONOMOUS  (drafts/, echo/, memory/, docs/_active/, etc.)
"""

import json
import os
import sys

# ── Paths ──

PROJECT_ROOT = os.environ.get("CLAUDE_PROJECT_DIR", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ── Zone Definitions ──

# Security Zone: HARD BLOCK — highest protection tier
ZONE_SECURITY_EXACT = [
    "CLAUDE.md",
]
ZONE_SECURITY_PREFIXES = [
    "product/",
    "assets/voice/",
]

# Zone A: BLOCK (deny) — explicit approval required
# .claude/ (except hooks/, plans/, skills/, settings files, violation-log.json)
ZONE_A_PREFIXES = [
    ".claude/",
]
ZONE_A_EXCEPTIONS = [
    ".claude/hooks/violation-log.json",
    ".claude/hooks/",
    ".claude/plans/",
    ".claude/skills/",
    ".claude/settings.json",
    ".claude/settings.local.json",
]

# Zone B: WARN (stdout text, no block) — propose before modifying
# docs/ (except docs/_active/), echo/VISION.md
ZONE_B_PREFIXES = [
    "docs/",
]
ZONE_B_EXACT = [
    "echo/VISION.md",
]
ZONE_B_EXCEPTIONS = [
    "docs/_active/",
]

# Zone C: AUTONOMOUS — everything not matched above (pass-through)

# ── Override Mechanism ──
# File-based: write target to .claude/hooks/.zone-override to bypass.
# Echo creates the file after the operator approves, hook reads and auto-deletes after use.
# Examples (file contents):
#   CLAUDE.md              → allows editing CLAUDE.md (one use)
#   product/               → allows editing anything in product/ (one use)
#   security               → allows ALL Security zone edits (one use)
#   zone-a                 → allows ALL Zone A edits (one use)
#
# Only use when the operator has explicitly approved the edit in conversation.
# Override is logged to stdout so the record exists.

OVERRIDE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".zone-override")

def check_override(rel_path):
    """Check if .zone-override file permits this path. Auto-deletes after use."""
    try:
        with open(OVERRIDE_FILE, "r") as f:
            override = f.read().strip()
    except (FileNotFoundError, PermissionError):
        return False
    if not override:
        return False
    override_lower = override.lower()
    rel_lower = rel_path.lower()
    matched = False
    # Exact file match
    if rel_lower == override_lower or rel_lower.endswith("/" + override_lower):
        matched = True
    # Prefix match (e.g. "product/")
    elif rel_lower.startswith(override_lower):
        matched = True
    # Zone-level override
    elif override_lower == "security" and check_zone_security(rel_path):
        matched = True
    elif override_lower == "zone-a" and check_zone_a(rel_path):
        matched = True
    if matched:
        # Auto-delete override file (single use)
        try:
            os.remove(OVERRIDE_FILE)
        except OSError:
            pass
        # Audit trail — log to sentinel override log
        try:
            from datetime import datetime
            log_path = os.path.join(os.path.dirname(OVERRIDE_FILE), "sentinel-override-log.json")
            log_entries = []
            if os.path.exists(log_path):
                with open(log_path, "r") as lf:
                    log_entries = json.loads(lf.read() or "[]")
            log_entries.append({
                "timestamp": datetime.now().isoformat(),
                "type": "zone-override",
                "path": rel_path,
                "override_value": override,
            })
            with open(log_path, "w") as lf:
                lf.write(json.dumps(log_entries, indent=2))
        except Exception:
            pass  # Audit failure should not block the approved edit
        print(f"ZONE OVERRIDE: {rel_path} — approved via .zone-override={override}")
        return True
    return False


def resolve_relative_path(file_path):
    """Resolve file_path to a relative path from PROJECT_ROOT."""
    file_path = os.path.normpath(file_path)
    project_root = os.path.normpath(PROJECT_ROOT)

    if os.path.isabs(file_path):
        if file_path.startswith(project_root + os.sep):
            return file_path[len(project_root) + 1:]
        elif file_path == project_root:
            return ""
        else:
            # Outside project root — pass-through (Zone C)
            return None
    return file_path


def check_zone_security(rel_path):
    """Check if path is in Security Zone. Returns reason string or None."""
    for exact in ZONE_SECURITY_EXACT:
        if rel_path.lower() == exact.lower():
            return f"SECURITY ZONE: {exact} is in the highest protection tier. Requires explicit operator approval."

    for prefix in ZONE_SECURITY_PREFIXES:
        if rel_path.startswith(prefix):
            return f"SECURITY ZONE: {rel_path} is in {prefix} — highest protection tier. Requires explicit operator approval."

    return None


def check_zone_a(rel_path):
    """Check if path is in Zone A. Returns reason string or None."""
    # Check exceptions first
    for exc in ZONE_A_EXCEPTIONS:
        if rel_path == exc or rel_path.startswith(exc) or rel_path == exc.rstrip("/"):
            return None

    for prefix in ZONE_A_PREFIXES:
        if rel_path.startswith(prefix):
            return f"ZONE A PROTECTED: {rel_path} is in {prefix} — requires explicit operator approval. Ask before modifying."

    return None


def check_zone_b(rel_path):
    """Check if path is in Zone B. Returns warning string or None."""
    # Check exceptions first
    for exc in ZONE_B_EXCEPTIONS:
        if rel_path == exc or rel_path.startswith(exc) or rel_path == exc.rstrip("/"):
            return None

    for exact in ZONE_B_EXACT:
        if rel_path == exact:
            return f"ZONE B WARNING: {exact} is controlled. Propose the change to the operator before executing. Did you get approval?"

    for prefix in ZONE_B_PREFIXES:
        if rel_path.startswith(prefix):
            return f"ZONE B WARNING: {rel_path} is controlled. Propose the change to the operator before executing. Did you get approval?"

    return None


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        sys.exit(0)

    rel_path = resolve_relative_path(file_path)

    # Outside project root or empty — pass-through (Zone C)
    if rel_path is None or rel_path == "":
        sys.exit(0)

    # Override check — operator-approved bypass
    if check_override(rel_path):
        sys.exit(0)

    # Security Zone — HARD BLOCK (highest tier)
    security_reason = check_zone_security(rel_path)
    if security_reason:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": security_reason,
            }
        }
        print(json.dumps(output))
        sys.exit(0)

    # Zone A — BLOCK
    zone_a_reason = check_zone_a(rel_path)
    if zone_a_reason:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": zone_a_reason,
            }
        }
        print(json.dumps(output))
        sys.exit(0)

    # Zone B — WARN (plain text to stdout)
    zone_b_warning = check_zone_b(rel_path)
    if zone_b_warning:
        print(zone_b_warning)
        sys.exit(0)

    # Zone C — AUTONOMOUS (pass-through)
    sys.exit(0)


if __name__ == "__main__":
    main()
