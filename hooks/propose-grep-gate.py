#!/usr/bin/env python3
"""
Propose-Grep Gate — Blocks Write of new architecture/spec docs without
evidence the author checked existing work.

Runs as a PreToolUse hook before Write operations.
Scope: NEW files (not edits) in docs/_active/, memory/reference_*.md,
memory/feedback_*.md, and echo/*-audit-*.md.

Enforcement rule:
  New files in these paths MUST include YAML frontmatter with one of:
    - `supersedes:` <list of prior docs the new one replaces>
    - `sibling_specs:` <list of related existing docs reviewed>
    - `parent_arch:` <parent architecture doc this extends>
    - `reviewed_against:` <list of docs grepped for overlap>

This forces Echo to prove it checked prior art before proposing new.
Addresses the S246 pattern captured in
memory/feedback_check_existing_architecture_before_proposing.md
(LLM routing matrix re-proposed despite existing, Mac App Cowork framed
as separate layer despite existing arch, etc.)

Exit 0: allow
Exit 2 (stderr): block with message
"""

import json
import os
import re
import sys

PROJECT_ROOT = os.environ.get(
    "CLAUDE_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

# Paths where "check existing before writing" matters most
GUARDED_PREFIXES = [
    "docs/_active/",
]
GUARDED_PATTERNS = [
    re.compile(r"^memory/reference_[a-z_]+\.md$"),
    re.compile(r"^memory/feedback_[a-z_]+\.md$"),
    re.compile(r"^echo/[a-z0-9-]+-audit-[0-9-]+\.md$"),
]

# Frontmatter keys that count as "I checked prior work"
EVIDENCE_KEYS = [
    "supersedes",
    "sibling_specs",
    "parent_arch",
    "parent_doc",
    "reviewed_against",
]


def resolve_relative_path(file_path: str) -> str | None:
    """Resolve to a path relative to PROJECT_ROOT, or None if outside."""
    file_path = os.path.normpath(file_path)
    project_root = os.path.normpath(PROJECT_ROOT)
    if os.path.isabs(file_path):
        if file_path.startswith(project_root + os.sep):
            return file_path[len(project_root) + 1:]
        if file_path == project_root:
            return ""
        return None
    return file_path


def is_guarded_path(rel_path: str) -> bool:
    for prefix in GUARDED_PREFIXES:
        if rel_path.startswith(prefix):
            return True
    for pattern in GUARDED_PATTERNS:
        if pattern.match(rel_path):
            return True
    return False


def extract_frontmatter_keys(content: str) -> list[str]:
    """Return list of top-level YAML frontmatter keys."""
    if not content.startswith("---\n"):
        return []
    end = content.find("\n---\n", 4)
    if end < 0:
        end = content.find("\n---", 4)
        if end < 0:
            return []
    block = content[4:end]
    keys = []
    for line in block.split("\n"):
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:", line)
        if m:
            keys.append(m.group(1))
    return keys


def list_sibling_candidates(rel_path: str) -> list[str]:
    """For the guarded file, list existing files in the same directory
    (or matching similar pattern) that might be overlapping prior work."""
    abs_dir = os.path.join(PROJECT_ROOT, os.path.dirname(rel_path))
    if not os.path.isdir(abs_dir):
        return []
    try:
        files = sorted(os.listdir(abs_dir))
    except OSError:
        return []
    base = os.path.basename(rel_path)
    # Strip trailing date / session suffix for keyword fuzzing
    kw = re.sub(r"-\d{4}-\d{2}-\d{2}.*$", "", base)
    kw = re.sub(r"\.md$", "", kw)
    tokens = [t for t in re.split(r"[-_]+", kw) if len(t) >= 4]
    hits = []
    for f in files:
        if f == base:
            continue
        if not f.endswith(".md"):
            continue
        for t in tokens:
            if t.lower() in f.lower():
                hits.append(os.path.join(os.path.dirname(rel_path), f))
                break
    return hits[:10]


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = payload.get("tool_name") or payload.get("tool")
    if tool != "Write":
        sys.exit(0)

    args = payload.get("tool_input") or payload.get("arguments") or {}
    file_path = args.get("file_path") or args.get("path")
    content = args.get("content", "")
    if not file_path:
        sys.exit(0)

    rel = resolve_relative_path(file_path)
    if rel is None or not is_guarded_path(rel):
        sys.exit(0)

    # Only gate NEW files. If the file already exists on disk, this is an
    # edit (even via Write), so the check-existing concern doesn't apply.
    abs_target = os.path.join(PROJECT_ROOT, rel) if not os.path.isabs(file_path) else file_path
    if os.path.exists(abs_target):
        sys.exit(0)

    keys = extract_frontmatter_keys(content)
    evidence = [k for k in keys if k in EVIDENCE_KEYS]
    if evidence:
        # Author explicitly cited prior work via frontmatter — allow.
        sys.exit(0)

    # No evidence — block with help.
    siblings = list_sibling_candidates(rel)
    msg_lines = [
        f"[PROPOSE-GREP-GATE] Blocking new file: {rel}",
        "",
        "This path is guarded because new architecture/spec docs must prove",
        "the author checked existing work before proposing new. See",
        "memory/feedback_check_existing_architecture_before_proposing.md.",
        "",
        "To pass this gate, add YAML frontmatter with one of:",
        "  supersedes:     [<prior-doc-path>]    # this replaces those",
        "  sibling_specs:  [<related-doc-path>]  # related work reviewed",
        "  parent_arch:    <parent-doc-path>     # extends this parent",
        "  reviewed_against: [<path1>, <path2>]  # explicit grep evidence",
    ]
    if siblings:
        msg_lines += [
            "",
            "Existing files in the same directory that share keywords with",
            f"`{os.path.basename(rel)}` — READ BEFORE writing the new one:",
        ]
        for s in siblings:
            msg_lines.append(f"  - {s}")
    else:
        msg_lines += [
            "",
            "No keyword-matching siblings found in the target directory.",
            "Still add one of the evidence keys to certify you checked.",
        ]
    msg_lines += [
        "",
        "If this IS genuinely new (no prior coverage), add:",
        "  reviewed_against: [grepped, nothing-found]",
        "as explicit certification and retry.",
    ]

    print("\n".join(msg_lines), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
