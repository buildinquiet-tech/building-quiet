#!/usr/bin/env python3
"""
stale-citation-sentinel.py — PostToolUse hook (Write|Edit)

Catches decision-grade artifacts citing memory files >7 days old without a
re-verification annotation in ±3 lines of the citation.

Symmetric pair with capitulation-sentinel.py:
  - capitulation-sentinel catches under-confidence (Echo agreeing without evidence)
  - stale-citation-sentinel catches over-confidence on stale evidence

Spec: docs/superpowers/specs/2026-05-01-stale-citation-sentinel-spec.md
Enforces: feedback_billing_state_freshness.md (S307) + LAW 4 (Verify Before Citing)
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
STALENESS_DAYS = 7
DECISION_GRADE_PATTERNS = [
    r"memory/decisions\.md$",
    r"echo/decisions/[\w_-]+\.md$",
    r".*verdict.*\.md$",
    r".*decision.*\.md$",
]
CITATION_PATTERN = re.compile(
    r"(memory/[\w_-]+\.md|echo/cfo/[\w_/-]+\.md|echo/scrape/[\w_/-]+\.md|docs/_active/[\w_-]+\.md)"
)
ANNOTATION_PATTERN = re.compile(
    r"(?i)(verified|fresh[\s-]?check|re[\s-]?verified|empirical|today|current state confirmed|state_verified_at|verification_method|live[\s-]?fetch|empirical[\s-]?check)"
)
OVERRIDE_ENV = "SENTINEL_OVERRIDE"
OVERRIDE_VALUE = "stale-citation"
TELEMETRY_LOG = Path(PROJECT_DIR) / ".claude/hooks/sentinel-override-log.json"
ANNOTATION_WINDOW = 3  # lines on each side of citation


def is_decision_grade(path: str) -> bool:
    if not path:
        return False
    return any(re.search(p, path) for p in DECISION_GRADE_PATTERNS)


def find_stale_citations(content: str):
    """Return list of (cited_path, days_old, mtime) for stale + un-annotated citations."""
    if not content:
        return []
    stale = []
    cutoff = time.time() - (STALENESS_DAYS * 86400)
    lines = content.split("\n")
    seen = set()
    for line_idx, line in enumerate(lines):
        for match in CITATION_PATTERN.finditer(line):
            cited_path = match.group(1)
            if cited_path in seen:
                continue
            full_path = Path(PROJECT_DIR) / cited_path
            if not full_path.exists():
                continue
            mtime = full_path.stat().st_mtime
            if mtime >= cutoff:
                continue
            window_start = max(0, line_idx - ANNOTATION_WINDOW)
            window_end = min(len(lines), line_idx + ANNOTATION_WINDOW + 1)
            window_text = "\n".join(lines[window_start:window_end])
            if ANNOTATION_PATTERN.search(window_text):
                continue
            days_old = (time.time() - mtime) / 86400
            stale.append((cited_path, days_old, mtime))
            seen.add(cited_path)
    return stale


def log_override(turn_id: str, target_path: str, cited_paths: list):
    try:
        existing = json.loads(TELEMETRY_LOG.read_text()) if TELEMETRY_LOG.exists() else []
    except Exception:
        existing = []
    existing.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "turn_id": turn_id,
        "target_path": target_path,
        "cited_paths": cited_paths,
        "override_reason": "stale-citation",
    })
    try:
        TELEMETRY_LOG.write_text(json.dumps(existing, indent=2))
    except Exception:
        pass


def main():
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    target_path = tool_input.get("file_path", "")
    if not is_decision_grade(target_path):
        sys.exit(0)

    if tool_name == "Write":
        content = tool_input.get("content", "")
    else:
        content = tool_input.get("new_string", "")

    stale = find_stale_citations(content)
    if not stale:
        sys.exit(0)

    if os.environ.get(OVERRIDE_ENV) == OVERRIDE_VALUE:
        log_override(
            turn_id=hook_input.get("turn_id", "unknown"),
            target_path=target_path,
            cited_paths=[c[0] for c in stale],
        )
        sys.exit(0)

    msg = [
        f"[STALE-CITATION CHECK] Decision-grade artifact ({target_path}) cites memory file(s) >{STALENESS_DAYS}d old without re-verification annotation:",
    ]
    for cited_path, days_old, mtime in stale:
        iso = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
        msg.append(f"  - {cited_path} (mtime: {iso}, age: {days_old:.1f}d)")
    msg.append("")
    msg.append("Per feedback_billing_state_freshness.md (S307) + LAW 4 (Verify Before Citing): re-verify empirically before citing for forward decisions.")
    msg.append("")
    msg.append("Three resolution paths:")
    msg.append("  (a) Re-verify and update citation with fresh state")
    msg.append("  (b) Add re-verification annotation near citation (e.g., \"verified S### YYYY-MM-DD via {method}\")")
    msg.append(f"  (c) Override if intentional: {OVERRIDE_ENV}={OVERRIDE_VALUE}")
    msg.append("")
    msg.append("Hook spec: docs/superpowers/specs/2026-05-01-stale-citation-sentinel-spec.md")

    print("\n".join(msg), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
