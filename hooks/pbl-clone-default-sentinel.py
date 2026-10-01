#!/usr/bin/env python3
"""pbl-clone-default-sentinel.py — PreToolUse Edit/Write hook.

Enforces LAW 19 §1 clone-first doctrine: every storyboard / reel-script /
brief in drafts/storyboard/** or drafts/reels/** must declare
`clone_source:` in frontmatter.

Carve-out: files whose mtime predates LAW 19 ratification (2026-05-09)
are legacy-exempt. New files default to no carve-out.

Override: SENTINEL_OVERRIDE=pbl-clone-default (one call only)

Spec: docs/superpowers/specs/2026-05-09-law-19-pbl-catalog-as-content-doctrine.md
Action item: AI-358-02 (S358 PM 2026-05-15)
Shipped: S359 2026-05-16 (Sat, deadline Fri 5/22 — 6d early)
"""
import json
import os
import re
import sys
from datetime import datetime


HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
FIRE_LOG = os.path.join(HOOKS_DIR, "pbl-clone-default-sentinel-fire-log.jsonl")

# LAW 19 ratified 2026-05-09. Files with mtime BEFORE this are legacy-exempt.
LAW19_RATIFICATION_TS = datetime(2026, 5, 9).timestamp()

# Watched path fragments (normalized to forward slashes)
WATCHED_PATH_FRAGMENTS = ("/drafts/storyboard/", "/drafts/reels/")

# Frontmatter detection: opening `---\n...\n---` block at top of file
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)

# clone_source field: matches `clone_source: <non-empty value>` in frontmatter
CLONE_SOURCE_RE = re.compile(r"^clone_source:\s*\S+", re.MULTILINE)


def log_fire(decision, reason="", **extra):
    """Append one JSONL record per meaningful invocation. Never raises."""
    rec = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hook": "pbl-clone-default-sentinel",
        "decision": decision,
        "reason": reason,
    }
    if extra:
        rec.update(extra)
    try:
        with open(FIRE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def is_storyboard_path(file_path: str) -> bool:
    """True iff path is inside a LAW-19-scoped drafts directory."""
    norm = (file_path or "").replace("\\", "/")
    return any(frag in norm for frag in WATCHED_PATH_FRAGMENTS)


def has_clone_source(content: str) -> bool:
    """True iff frontmatter declares a non-empty clone_source field."""
    if not content:
        return False
    fm_match = FRONTMATTER_RE.search(content)
    if not fm_match:
        return False
    return bool(CLONE_SOURCE_RE.search(fm_match.group(1)))


def is_legacy_file(file_path: str) -> bool:
    """Files with mtime predating LAW 19 ratification are exempt."""
    if not file_path or not os.path.exists(file_path):
        return False
    try:
        return os.path.getmtime(file_path) < LAW19_RATIFICATION_TS
    except OSError:
        return False


def edit_introduces_clone_source(new_string: str) -> bool:
    """True iff the Edit's new_string fragment contains a `clone_source:`
    line. Edit new_strings are fragments not full files — they typically
    lack the frontmatter `---` delimiters that `has_clone_source` requires.
    """
    if not new_string:
        return False
    return bool(re.search(r"^clone_source:\s*\S+", new_string, re.MULTILINE))


def resulting_content(tool_name: str, tool_input: dict, file_path: str) -> str:
    """Return the content that would exist AFTER the Edit/Write applies.

    For Write: the new_content. For Edit: the existing file (caller
    should check edit_introduces_clone_source FIRST for the add-by-Edit
    case before falling here).
    """
    if tool_name == "Write":
        return tool_input.get("content", "") or ""

    new_string = tool_input.get("new_string", "") or ""
    if file_path and os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return new_string

    return new_string


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "pbl-clone-default":
        log_fire("pass", "override_env")
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        sys.exit(0)

    tool_input = data.get("tool_input", {}) or {}
    file_path = str(tool_input.get("file_path", "") or "")

    if not is_storyboard_path(file_path):
        sys.exit(0)

    # Legacy carve-out: pre-LAW-19 files exempt
    if is_legacy_file(file_path):
        log_fire("pass", "legacy_carve_out",
                 file_path=file_path, tool=tool_name)
        sys.exit(0)

    # Edit-direct-add path: the new_string itself introduces clone_source
    if tool_name == "Edit":
        new_string = tool_input.get("new_string", "") or ""
        if edit_introduces_clone_source(new_string):
            log_fire("pass", "clone_source_in_edit_new_string",
                     file_path=file_path)
            sys.exit(0)

    content = resulting_content(tool_name, tool_input, file_path)
    if has_clone_source(content):
        log_fire("pass", "clone_source_present",
                 file_path=file_path, tool=tool_name)
        sys.exit(0)

    msg = (
        f"[PBL CLONE-DEFAULT GATE] LAW 19 §1 violation.\n\n"
        f"File: {file_path}\n"
        f"Tool: {tool_name}\n\n"
        "Per LAW 19 (PBL Catalog as Content Doctrine, S339 2026-05-09), "
        "every storyboard / reel-script / brief must declare "
        "`clone_source:` in frontmatter. The first question for any new "
        "post: *Is this a clone of a proven viral video?* (PBL §21.5).\n\n"
        "Required field (one of):\n"
        "  clone_source: PBL §X.Y / @creator [proof metric]\n"
        "  clone_source: NOVEL — synthesis_justification: <evidence>\n\n"
        "Add the field to your frontmatter and re-attempt.\n\n"
        "Doctrine: docs/_active/LAW.md §LAW 19. PBL Bible: "
        "memory/reference_pbl.md.\n\n"
        "Override (one call): SENTINEL_OVERRIDE=pbl-clone-default"
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        }
    }
    log_fire("block", "missing_clone_source",
             file_path=file_path, tool=tool_name)
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
