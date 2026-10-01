#!/usr/bin/env python3
"""Closing Integrity Sentinel — Stop hook.

Defends against the failure mode documented in S341 audit
(`echo/reviews/closing-integrity-audit-2026-05-10-S341.md`):
content-rich /closing blocks improvising numeric/state claims from narrative
recall instead of grep-derived probes. Yesterday's S340 closing had 6 of 11
claims defective (54.5%) — every defect trivially auto-checkable.

Trigger: Stop hook fires after every assistant turn.
Behavior:
  1. Find today's handoff file
  2. Locate `## CLOSING — RUN AT` block
  3. Skip if block absent OR if block is marker-only boilerplate
  4. For each auto-checkable claim in the block, re-derive ground truth
  5. If any defect: write report to echo/sentinel/closing-integrity-{date}.md
     + emit reminder to surface in next /echo

Pair-rule with `feedback_closing_claims_must_be_grep_derived.md` (S341).

Override: SENTINEL_OVERRIDE=closing-integrity
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[2]))
SENTINEL_DIR = REPO / "echo" / "sentinel"
HANDOFF_FRESHNESS_MIN = 60  # only audit if closing block written in last N minutes


def _today_handoff() -> Path:
    return REPO / "echo" / "handoffs" / f"{dt.date.today().isoformat()}.md"


def _extract_closing_block(handoff: Path) -> str | None:
    if not handoff.exists():
        return None
    text = handoff.read_text(encoding="utf-8", errors="replace")
    m = re.search(
        r"^## CLOSING — RUN AT.*?(?=\n## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return m.group(0) if m else None


def _block_is_marker_only(block: str) -> bool:
    """Marker-only closings have no numeric/state claims to audit."""
    body = re.sub(r"^## CLOSING — RUN AT.*?\n", "", block, count=1, flags=re.MULTILINE)
    body = body.strip()
    return not body or body == "Day fully closed. /save-state is the right tool for any post-closing extension work."


def _file_age_minutes(path: Path) -> float:
    if not path.exists():
        return 1e9
    return (dt.datetime.now().timestamp() - path.stat().st_mtime) / 60.0


def _import_snapshot():
    sys.path.insert(0, str(REPO / "scripts" / "closing"))
    try:
        import closing_state_snapshot  # type: ignore
        return closing_state_snapshot
    except ImportError:
        return None


def audit_block(block: str, handoff: Path) -> list[dict]:
    """Audit every auto-checkable claim. Return list of {claim, expected, actual} defects."""
    snap = _import_snapshot()
    if snap is None:
        return [{"claim": "infra", "expected": "snapshot helper importable", "actual": "scripts/closing/closing_state_snapshot.py missing"}]

    defects: list[dict] = []

    # Save-state count claim — pattern: "Four save-state blocks" or "N save-state blocks"
    actual_ss = snap.save_state_count(handoff)
    m = re.search(r"\b(Zero|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|\d+)\s+save-state blocks?\b", block, re.IGNORECASE)
    if m:
        word2num = {"Zero": 0, "One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5,
                    "Six": 6, "Seven": 7, "Eight": 8, "Nine": 9, "Ten": 10}
        raw = m.group(1)
        claimed = word2num.get(raw.capitalize(), int(raw) if raw.isdigit() else None)
        if claimed is not None and claimed != actual_ss:
            defects.append({"claim": "save_state_count",
                            "claimed": claimed, "actual": actual_ss,
                            "evidence": f"grep -cE '^## S\\d+.*save-state' {handoff.relative_to(REPO)}"})

    # main HEAD claim — handles "main: HEAD = SHA", "main HEAD = SHA", "main HEAD: SHA"
    git_ = snap.git_state()
    head_actual = git_["head_sha"]
    head_matches = re.findall(
        r"main[^\n]*?HEAD\s*[=:]\s*`?([0-9a-f]{7,})",
        block,
        re.IGNORECASE,
    )
    if head_matches:
        for claimed in head_matches:
            if not head_actual.startswith(claimed[:7]) and not claimed.startswith(head_actual[:7]):
                defects.append({"claim": "main_head",
                                "claimed": claimed, "actual": head_actual,
                                "evidence": "git rev-parse --short HEAD"})

    # Branch fabrication — pattern: "branch-name: <sha> ahead of main" or branch namespace mentions
    branch_mentions = re.findall(r"\n[\s-]*([a-z][a-z0-9-]*-S\d{3})[:\s]", block)
    if branch_mentions:
        import subprocess
        existing = subprocess.run(
            ["git", "branch", "-a"], cwd=REPO, capture_output=True, text=True
        ).stdout
        for bn in set(branch_mentions):
            if bn not in existing:
                defects.append({"claim": "branch_exists",
                                "claimed": bn, "actual": "branch not in `git branch -a` or reflog",
                                "evidence": f"git branch -a | grep {bn}"})

    # Quality-gate violation claim — pattern: "0/0 quality-gate" or "N quality-gate violations"
    qg = snap.quality_gate_7d()
    qg_matches = re.findall(r"(\d+)\s*/\s*(\d+)\s+quality-gate\s+violations", block, re.IGNORECASE)
    for claimed_v, claimed_e in qg_matches:
        if int(claimed_v) != qg["violations"] or int(claimed_e) != qg["entries"]:
            defects.append({"claim": "quality_gate_7d",
                            "claimed": f"{claimed_v}/{claimed_e}",
                            "actual": f"{qg['violations']}/{qg['entries']}",
                            "evidence": "Python count on .claude/hooks/violation-log.json (7d cutoff)"})

    # Time-estimate miss rate — pattern: "X.X% time-estimate" or "XX% time-estimate miss"
    te = snap.time_estimate_7d()
    te_matches = re.findall(r"(\d+(?:\.\d+)?)\s*%\s+time[-\s]?estimate", block, re.IGNORECASE)
    for claimed_pct in te_matches:
        actual = te.get("miss_rate_pct", "?")
        if isinstance(actual, (int, float)):
            try:
                if abs(float(claimed_pct) - actual) > 2.0:  # tolerance: 2 percentage points
                    defects.append({"claim": "time_estimate_miss_rate",
                                    "claimed": f"{claimed_pct}%", "actual": f"{actual}%",
                                    "evidence": "Python count on echo/time-estimate-log.jsonl verdicts (7d)"})
            except ValueError:
                pass

    # Library reindex — pattern: "1,193 files" or "N,NNN files"
    lib = snap.library_state()
    lib_matches = re.findall(r"(\d[\d,]*)\s+files\s*/\s*(\d+)\s+errors", block, re.IGNORECASE)
    for claimed_files_s, claimed_errors_s in lib_matches:
        claimed_files = int(claimed_files_s.replace(",", ""))
        if isinstance(lib.get("total_files"), int) and claimed_files != lib["total_files"]:
            defects.append({"claim": "library_files",
                            "claimed": claimed_files, "actual": lib["total_files"],
                            "evidence": "jq .total_files < .library/last-indexed.json"})

    return defects


def write_report(defects: list[dict], block: str) -> Path:
    SENTINEL_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    report = SENTINEL_DIR / f"closing-integrity-{today}.md"
    lines = [
        f"# /closing Integrity Sentinel — {today}",
        "",
        f"**Audited at:** {dt.datetime.now().isoformat()}",
        f"**Defects found:** {len(defects)}",
        f"**Source doctrine:** `feedback_closing_claims_must_be_grep_derived.md` (S341)",
        "",
        "## Defects",
        "",
        "| # | Claim | Closing said | Ground truth | Re-derive command |",
        "|:-:|-------|--------------|--------------|-------------------|",
    ]
    for i, d in enumerate(defects, 1):
        claim = d.get("claim", "?")
        claimed = d.get("claimed", "?")
        actual = d.get("actual", "?")
        evidence = d.get("evidence", "?")
        lines.append(f"| {i} | {claim} | `{claimed}` | `{actual}` | `{evidence}` |")
    lines.extend([
        "",
        "## Captured closing block",
        "",
        "```markdown",
        block.strip(),
        "```",
        "",
        "## Next-session action",
        "",
        "`/echo` reads this file at session start. Correct the inherited facts in any briefing.",
        "Fix the underlying closing block manually OR re-run /closing with the helper:",
        "",
        "```bash",
        "python3 scripts/closing/closing_state_snapshot.py >> echo/handoffs/$(date +%Y-%m-%d).md",
        "```",
    ])
    report.write_text("\n".join(lines) + "\n")
    return report


def main():
    if os.environ.get("SENTINEL_OVERRIDE") == "closing-integrity":
        sys.exit(0)

    handoff = _today_handoff()
    block = _extract_closing_block(handoff)
    if not block:
        sys.exit(0)
    if _block_is_marker_only(block):
        sys.exit(0)
    # Only audit closings written/modified recently (handoff file mtime <60min)
    if _file_age_minutes(handoff) > HANDOFF_FRESHNESS_MIN:
        sys.exit(0)

    defects = audit_block(block, handoff)
    if not defects:
        sys.exit(0)

    report = write_report(defects, block)
    # Emit to stderr so it surfaces to operator inline + via /echo next session
    print(
        f"⚠️ CLOSING INTEGRITY SENTINEL — {len(defects)} defective claim(s) in today's /closing block.\n"
        f"Report: {report.relative_to(REPO)}\n"
        f"Doctrine: feedback_closing_claims_must_be_grep_derived.md (S341)\n"
        f"Fix: re-run /closing OR patch the block manually using closing_state_snapshot.py.",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
