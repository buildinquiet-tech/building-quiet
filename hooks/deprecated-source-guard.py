#!/usr/bin/env python3
"""Deprecated-Source Guard — PreToolUse Read hook.

Refuses Read tool calls against files graded DEPRECATED in
`docs/_active/source-registry.yml`. Names the canonical replacement(s)
in the refusal message. Bypass via `READ_DEPRECATED_REASON` env var;
every bypass logged to `.claude/hooks/deprecated-read-log.json` (JSONL).

Built S343 2026-05-12 per Ultrathink Review v2 plan Part 2.

Pair-rules:
  - feedback_closing_claims_must_be_grep_derived.md (S341)
  - feedback_brief_claims_must_be_api_or_grep_derived.md (S342)
  - feedback_status_claims_must_be_grep_derived.md (S342)

Behavior:
  1. Parse tool_input — extract target file path.
  2. Resolve to repo-relative path.
  3. Load source-registry.yml; look up grade.
  4. If grade=DEPRECATED:
       - If READ_DEPRECATED_REASON set: ALLOW + append to log.
       - Else: REFUSE (exit 2) with replacement-pointer message.
  5. Else: ALLOW.

Override: SENTINEL_OVERRIDE=deprecated-source-guard (always-allow, also logged).

Exit codes:
  0 — allow (path not graded, or graded CANONICAL/ADVISORY, or bypass with reason)
  2 — block (graded DEPRECATED, no bypass)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    # yaml is in stdlib environments via PyYAML; if missing, fail-open
    # rather than block Read globally. This is safer than fail-closed.
    sys.exit(0)


REPO = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[2]))
REGISTRY = REPO / "docs" / "_active" / "source-registry.yml"
LOG = REPO / ".claude" / "hooks" / "deprecated-read-log.json"


def _load_registry() -> dict:
    """Return the {path: entry} sources map. Empty dict on any failure."""
    if not REGISTRY.exists():
        return {}
    try:
        with REGISTRY.open() as f:
            docs = list(yaml.safe_load_all(f))
    except Exception:
        return {}
    for d in docs:
        if isinstance(d, dict) and "sources" in d:
            sources = d["sources"]
            if isinstance(sources, dict):
                return sources
    return {}


def _normalize_path(raw: str) -> str | None:
    """Resolve raw tool input path to a repo-relative POSIX path string."""
    if not raw:
        return None
    try:
        p = Path(raw)
        if not p.is_absolute():
            p = (REPO / p).resolve(strict=False)
        else:
            p = p.resolve(strict=False)
        repo_resolved = REPO.resolve(strict=False)
        rel = p.relative_to(repo_resolved)
        return str(rel)
    except (ValueError, OSError):
        return None


def _append_log(event: dict) -> None:
    """Append a JSONL event to the deprecated-read-log."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass  # never let logging failures break the hook


def _format_block_message(rel_path: str, entry: dict) -> str:
    superseded = entry.get("superseded_by") or []
    since = entry.get("deprecated_since", "unknown date")
    note = entry.get("note", "")
    lines = [
        f"[DEPRECATED-SOURCE-GUARD] Read refused: {rel_path}",
        "",
        f"This file was DEPRECATED on {since}. Reading it leaks stale state.",
    ]
    if superseded:
        lines += ["", "Canonical replacement(s):"]
        for s in superseded:
            lines.append(f"  - {s}")
    if note:
        lines += ["", f"Context: {note}"]
    lines += [
        "",
        "If you have a legitimate reason to read this DEPRECATED file",
        "(audit, archaeology, migration sweep), retry with:",
        '  READ_DEPRECATED_REASON="<short reason>" Read <path>',
        "Bypass usage is logged to .claude/hooks/deprecated-read-log.json",
        "and audited for normalization drift.",
    ]
    return "\n".join(lines)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if (payload.get("tool_name") or payload.get("tool")) != "Read":
        sys.exit(0)

    args = payload.get("tool_input") or payload.get("arguments") or {}
    raw_path = args.get("file_path") or args.get("path")
    rel = _normalize_path(raw_path) if raw_path else None
    if not rel:
        sys.exit(0)

    sources = _load_registry()
    entry = sources.get(rel)
    if not entry or not isinstance(entry, dict):
        sys.exit(0)

    grade = entry.get("grade", "").upper()
    if grade != "DEPRECATED":
        sys.exit(0)

    reason = os.environ.get("READ_DEPRECATED_REASON", "").strip()
    override = os.environ.get("SENTINEL_OVERRIDE", "").strip()

    timestamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    session = os.environ.get("ECHO_SESSION", "unknown")

    if reason or override == "deprecated-source-guard":
        _append_log({
            "ts": timestamp,
            "session": session,
            "file": rel,
            "reason": reason or "SENTINEL_OVERRIDE=deprecated-source-guard",
            "bypass_type": "reason" if reason else "sentinel_override",
        })
        sys.exit(0)

    _append_log({
        "ts": timestamp,
        "session": session,
        "file": rel,
        "action": "blocked",
    })
    print(_format_block_message(rel, entry), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
