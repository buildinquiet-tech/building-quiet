#!/usr/bin/env python3
"""
LAW Enforcement Manifest Gate -- PreToolUse hook.

Blocks Edit/Write to LAW.md and docs/superpowers/specs/*-law-*.md unless every
LAW number referenced in the change has a corresponding row in the manifest at
docs/_active/law-enforcement-manifest.md.

Closes the failure class: LAW 19 was written S339 and audited S358 -- 6 days
later -- at 30% compliance. No enforcement plan landed between write and audit.
This hook forces every new LAW (or modified LAW spec) to declare its enforcement
plan in the manifest BEFORE the LAW prose can ship.

Scope:
  Triggers ONLY on writes to:
    - docs/_active/LAW.md
    - docs/superpowers/specs/*-law-*.md  (any LAW spec file)

  Exempt:
    - docs/_active/law-enforcement-manifest.md itself (chicken-and-egg)

Detection logic:
  - Edit: extract LAW numbers from new_string via `^### LAW (\\d+)` regex
  - Write: extract from full content via same regex
  - For each extracted LAW number: check manifest contains `| {N} |` row pattern
  - If ANY referenced LAW is missing from manifest -> BLOCK

Bypass:
  Set LAW_ENFORCEMENT_DEFERRED_REASON="<rationale>" env var. Logged to
  .claude/hooks/law-manifest-gate-log.jsonl for weekly audit.

Exit codes:
  0 -- allow (no LAW refs, all refs present, exempt path, or bypass with reason)
  2 -- block (stderr message names missing LAW + manifest path)

Spec: docs/superpowers/specs/2026-05-15-S358-doctrine-enforcement-loop.md
Sibling pattern: .claude/hooks/propose-grep-gate.py
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.environ.get(
    "CLAUDE_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

MANIFEST_REL = "docs/_active/law-enforcement-manifest.md"
LAW_MD_REL = "docs/_active/LAW.md"
SPEC_DIR_REL = "docs/superpowers/specs/"
SPEC_LAW_PATTERN = re.compile(r"^docs/superpowers/specs/[\d-]+-?(?:S\d+-)?law-(\d+)-")

LAW_REF_RE = re.compile(r"^###\s+LAW\s+(\d+)\s+[—–-]", re.MULTILINE)
MANIFEST_ROW_RE_TEMPLATE = r"^\|\s*{n}\s*\|"

BYPASS_ENV = "LAW_ENFORCEMENT_DEFERRED_REASON"
LOG_PATH = os.path.join(
    PROJECT_ROOT, ".claude", "hooks", "law-manifest-gate-log.jsonl"
)


def resolve_relative_path(file_path):
    file_path = os.path.normpath(file_path)
    project_root = os.path.normpath(PROJECT_ROOT)
    if os.path.isabs(file_path):
        if file_path.startswith(project_root + os.sep):
            return file_path[len(project_root) + 1:]
        if file_path == project_root:
            return ""
        return None
    return file_path


def is_in_scope(rel_path):
    if rel_path == MANIFEST_REL:
        return False
    if rel_path == LAW_MD_REL:
        return True
    if rel_path.startswith(SPEC_DIR_REL) and "law-" in os.path.basename(rel_path):
        return True
    return False


def extract_law_numbers_from_text(text):
    if not text:
        return set()
    return {int(m.group(1)) for m in LAW_REF_RE.finditer(text)}


def extract_law_number_from_spec_filename(rel_path):
    m = SPEC_LAW_PATTERN.match(rel_path)
    if m:
        return int(m.group(1))
    return None


def manifest_contains_law(manifest_text, law_n):
    pattern = re.compile(MANIFEST_ROW_RE_TEMPLATE.format(n=law_n), re.MULTILINE)
    return bool(pattern.search(manifest_text))


def read_manifest():
    manifest_abs = os.path.join(PROJECT_ROOT, MANIFEST_REL)
    if not os.path.exists(manifest_abs):
        return None
    try:
        with open(manifest_abs, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def log_event(event):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        event["ts"] = datetime.now(timezone.utc).isoformat()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_name = payload.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        return 0

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return 0

    rel_path = resolve_relative_path(file_path)
    if rel_path is None or not is_in_scope(rel_path):
        return 0

    referenced_laws = set()

    if tool_name == "Edit":
        new_string = tool_input.get("new_string", "")
        referenced_laws |= extract_law_numbers_from_text(new_string)
    else:
        content = tool_input.get("content", "")
        referenced_laws |= extract_law_numbers_from_text(content)

    spec_law = extract_law_number_from_spec_filename(rel_path)
    if spec_law is not None:
        referenced_laws.add(spec_law)

    if not referenced_laws:
        return 0

    bypass = os.environ.get(BYPASS_ENV, "").strip()
    if bypass:
        log_event({
            "event": "bypass",
            "tool": tool_name,
            "path": rel_path,
            "referenced_laws": sorted(referenced_laws),
            "reason": bypass,
        })
        return 0

    manifest_text = read_manifest()
    if manifest_text is None:
        sys.stderr.write(
            "[law-enforcement-manifest-gate] WARNING: manifest not found at "
            + MANIFEST_REL
            + ". Cannot verify LAW(s) "
            + str(sorted(referenced_laws))
            + ".\n"
        )
        log_event({
            "event": "manifest-missing",
            "tool": tool_name,
            "path": rel_path,
            "referenced_laws": sorted(referenced_laws),
        })
        return 0

    missing = sorted(
        n for n in referenced_laws if not manifest_contains_law(manifest_text, n)
    )
    if not missing:
        return 0

    missing_str = ", ".join("LAW " + str(n) for n in missing)
    sys.stderr.write(
        "[law-enforcement-manifest-gate] BLOCKING -- manifest missing rows for: "
        + missing_str
        + "\n\nTarget file: "
        + rel_path
        + "\nManifest path: "
        + MANIFEST_REL
        + "\n\nEvery LAW (new or modified) must declare its enforcement plan in the "
        + "manifest BEFORE the LAW prose can ship. Add a row with columns:\n"
        + "  | LAW # | Title | Tier | Persona | enforcement_method | hook_path | "
        + "audit_method | last_compliance_check | compliance_pct | status |\n\n"
        + "Bypass (single edit): set env var "
        + BYPASS_ENV
        + '="<rationale>"\n'
        + "  Bypasses are logged to .claude/hooks/law-manifest-gate-log.jsonl "
        + "for weekly audit.\n\n"
        + "See: docs/superpowers/specs/2026-05-15-S358-doctrine-enforcement-loop.md "
        + "Phase 1 spec for rationale.\n"
    )
    log_event({
        "event": "block",
        "tool": tool_name,
        "path": rel_path,
        "referenced_laws": sorted(referenced_laws),
        "missing": missing,
    })
    return 2


if __name__ == "__main__":
    sys.exit(main())
