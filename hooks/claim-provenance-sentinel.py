#!/usr/bin/env python3
"""Claim-Provenance Sentinel — Stop hook (umbrella, advisory tier).

Detects operational state claims in the assistant's last turn that lack
co-located source citation (file path, MCP/API call, or quote from a
canonical-graded source). Flags to `echo/sentinel/claim-provenance-{date}.md`
for the next /echo to surface.

Built S343 2026-05-12 per Ultrathink Review v2 plan Part 4.
Absorbs AI-342-10 (queue-state-claim-sentinel narrow spec) per
D-2026-0511-001 operator fold decision (Tue 5/12 AM).
Extended S347 2026-05-13 with subscription detectors and citation freshness checks.

Pair-rules:
  - feedback_closing_claims_must_be_grep_derived.md (S341)
  - feedback_brief_claims_must_be_api_or_grep_derived.md (S342)
  - feedback_status_claims_must_be_grep_derived.md (S342)

Tier policy:
  - ADVISORY (current, 7-day soak): write findings to sentinel dir, no block.
  - BLOCKING (after soak + tuning): set CLAIM_PROVENANCE_TIER=blocking in env
    OR change DEFAULT_TIER below. Soak start: 2026-05-12. Earliest review: 2026-05-19.

Override (current turn): SENTINEL_OVERRIDE=claim-provenance
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
DEFAULT_TIER = "advisory"  # promote to "blocking" after 7-day soak review

# ============================================================
# Detector registry
# ============================================================
# Each detector has:
#   id       — short name surfaced in flags
#   pattern  — compiled regex matching the claim pattern in prose
#   kind     — semantic class for grouping / metric surfacing
#
# Claims here are PATTERNS, not proofs. The provenance check decides
# whether each match is supported.

CLAIM_DETECTORS = [
    # ---- AI-342-10 absorbed: scheduler queue state ----
    {"id": "queue_state",
     "pattern": re.compile(r"\bqueue:\s*\d+\b", re.IGNORECASE),
     "kind": "scheduler"},
    {"id": "live_count",
     "pattern": re.compile(r"\b\d+\s+LIVE\b"),
     "kind": "scheduler"},
    {"id": "pending_count",
     "pattern": re.compile(r"\b\d+\s+PENDING\b"),
     "kind": "scheduler"},
    {"id": "scheduled_count",
     "pattern": re.compile(r"\b\d+\s+(?:posts?|schedules?|threads?)\s+scheduled\b", re.IGNORECASE),
     "kind": "scheduler"},
    {"id": "fired_count",
     "pattern": re.compile(r"\b\d+\s+(?:posts?|schedules?|fires?)\s+(?:fired|published)\b", re.IGNORECASE),
     "kind": "scheduler"},

    # ---- Sprint / action-item status ----
    {"id": "sprint_ratio",
     "pattern": re.compile(r"\b\d+/\d+\s+(?:KILLED|SHIPPED|DONE|MISSED|PENDING|ACTIVE)\b"),
     "kind": "sprint"},

    # ---- Sentinel / hook / feedback memo counts ----
    # Any numeric claim about sentinel/hook/memo count is an operational state
    # claim, regardless of surrounding verb order ("shipped 15 sentinels" vs
    # "15 sentinels shipped" both warrant provenance).
    {"id": "sentinels_shipped_count",
     "pattern": re.compile(r"\b\d+\s+sentinels?\b", re.IGNORECASE),
     "kind": "infra"},
    {"id": "hooks_count",
     "pattern": re.compile(r"\b\d+\s+hooks?\b(?=.{0,50}?(?:\.claude/hooks|shipped|added|removed|deleted|enforced|wired|registered|fired|active|in settings\.json))", re.IGNORECASE | re.DOTALL),
     "kind": "infra"},
    {"id": "feedback_memos_count",
     "pattern": re.compile(r"\b\d+\s+(?:feedback\s+memos?|judgment-tier|hard-tier)\b", re.IGNORECASE),
     "kind": "infra"},

    # ---- Brand / audience / revenue metric claims ----
    {"id": "follower_count",
     "pattern": re.compile(r"\b\d{2,}\s+followers?\b", re.IGNORECASE),
     "kind": "metric"},
    {"id": "revenue_dollar_claim",
     "pattern": re.compile(r"\$\d+(?:[.,]\d+)?(?:/mo|/yr|\s+MRR|\s+revenue|\s+sales)\b", re.IGNORECASE),
     "kind": "metric"},

    # ---- S347: Subscription claims ----
    # subscription_row_claim KILLED S383 — see claim-provenance-soak-extension-end-2026-05-28-S383.md
    {"id": "subscription_state_claim",
     "pattern": re.compile(
         r"\b(?:ACTIVE|CANCELLED|INVESTIGATE|PAUSED|DEPRECATED|KEEP|DROP)\s+"
         r"(?:per|via|since|effective)\s+",
         re.IGNORECASE),
     "kind": "subscription"},
]

# ============================================================
# Citation / provenance indicators
# ============================================================
# A claim is considered SUPPORTED if any one of these appears within
# CITATION_WINDOW characters before or after the match in the same paragraph.

CITATION_WINDOW = 400  # characters around the match to scan for citation

CITATION_PATTERNS = [
    # Explicit source citation in markdown footnote style
    re.compile(r"\[source:\s*[^\]]+\]", re.IGNORECASE),
    # MCP / API call reference within the same neighborhood
    re.compile(r"\bmcp__[a-z0-9_-]+__\w+", re.IGNORECASE),
    re.compile(r"\bblotato_list_schedules\b", re.IGNORECASE),
    re.compile(r"\bblotato_\w+\b", re.IGNORECASE),
    re.compile(r"\bGET\s+/\w+|\bPOST\s+/\w+", re.IGNORECASE),
    # Specific file path with .md or .py or .yml extension
    re.compile(r"\b[a-z0-9_./-]+\.(?:md|py|yml|json|jsonl|sh)\b", re.IGNORECASE),
    # git probe / grep command shown as evidence
    re.compile(r"```(?:bash|sh|shell)\b", re.IGNORECASE),
    re.compile(r"\bgrep\s+-[a-zE]+\s", re.IGNORECASE),
    re.compile(r"\bgit\s+(?:log|rev-parse|show|diff|status)\b", re.IGNORECASE),
    # Echo internal IDs (session, decision, action-item, LAW)
    re.compile(r"\bS\d{3}\b"),
    re.compile(r"\bD-\d{4}-\d{4}-\d{3}\b"),
    re.compile(r"\bAI-\d{3}-\d{2}\b"),
    re.compile(r"\bLAW\s+\d+\b", re.IGNORECASE),
    # Specific session-counter / commit-hash patterns
    re.compile(r"\bcommit\s+[a-f0-9]{7,}\b", re.IGNORECASE),
    # S347: Freshness anchors
    re.compile(r"\bactive-brief\b|\bcfo-brief\.md\b|\bcto-brief\.md\b|\bcmo-brief\.md\b|\bcso-brief\.md\b|\bgc-brief\.md\b|\bNOW\.md\b", re.IGNORECASE),
    re.compile(r"\b" + dt.date.today().strftime("%Y-%m-%d") + r"\b"),  # today's date
    re.compile(r"\bD-" + dt.date.today().strftime("%Y-%m") + r"-\d{3}\b"),  # this month's decisions
]

# ============================================================
# Citation freshness rules (S347)
# ============================================================
# For certain volatile claim kinds, file-path citations must be recent
# unless a strong "freshness anchor" is also present.

FRESHNESS_ANCHORS = [
    p for p in CITATION_PATTERNS if "brief" in p.pattern or "NOW.md" in p.pattern
    or dt.date.today().strftime("%Y-%m") in p.pattern
]

VOLATILITY_RULES = {
    "subscription": {
        "stale_prone_patterns": [
            re.compile(r"audit.*\.md$", re.IGNORECASE),
            re.compile(r"subscription-audit\.md$", re.IGNORECASE),
        ],
        "threshold_days": 3,
        "anchor_patterns": FRESHNESS_ANCHORS,
    },
    "scheduler": { # TODO per spec S347: implement freshness for scheduler claims
        "stale_prone_patterns": [re.compile(r"queue-status\.md"), re.compile(r"content-log\.md")],
        "threshold_days": 1,
        "anchor_patterns": [],  # TODO: MCP call OR today's date
    },
    "metric": { # TODO per spec S347: implement freshness for metric claims
        "stale_prone_patterns": [re.compile(r"\.csv$"), re.compile(r"performance-report")],
        "threshold_days": 7,
        "anchor_patterns": [],  # TODO: dashboard URL OR today's date
    },
    "sprint": { # TODO per spec S347: implement freshness for sprint claims
        "stale_prone_patterns": [re.compile(r"active-work\.md")],
        "threshold_days": 1,
        "anchor_patterns": [],  # TODO: session ID match (today's S###)
    },
}
FILE_PATH_PATTERN = next(p for p in CITATION_PATTERNS if ".md" in p.pattern)


def _read_transcript_last_assistant(transcript_path: str) -> str:
    """Read JSONL transcript backwards, return concatenated text of last assistant turn."""
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.exists():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
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
                if isinstance(blk, dict) and blk.get("type") == "text":
                    parts.append(blk.get("text", ""))
            return "\n".join(parts)
        return ""
    return ""


def _build_finding(finding_type: str, detector: dict, match: re.Match, text: str) -> dict:
    """Helper to construct a finding dictionary."""
    line_no = text[:match.start()].count("\n") + 1
    return {
        "finding_type": finding_type,
        "detector_id": detector["id"],
        "kind": detector["kind"],
        "match": match.group(0),
        "line": line_no,
        "context": text[max(0, match.start() - 60):min(len(text), match.end() + 60)].replace("\n", " "),
    }


def _find_unsupported_claims(text: str) -> list[dict]:
    """For each detector, find matches and check for provenance in window."""
    if not text:
        return []
    findings = []
    for detector in CLAIM_DETECTORS:
        for m in detector["pattern"].finditer(text):
            start = max(0, m.start() - CITATION_WINDOW)
            end = min(len(text), m.end() + CITATION_WINDOW)
            window = text[start:end]

            # Skip self-references to this hook or its findings file
            if "claim-provenance" in window.lower():
                continue

            citations_found = [p for p in CITATION_PATTERNS if p.search(window)]
            if not citations_found:
                findings.append(_build_finding("UNSUPPORTED", detector, m, text))
                continue

            claim_kind = detector["kind"]
            if claim_kind not in VOLATILITY_RULES:
                continue  # Has citation, no freshness rule -> supported

            rule = VOLATILITY_RULES[claim_kind]
            is_supported_by_fresh_source = False

            # Check for anchors first - they provide unconditional support
            if any(p.search(window) for p in rule["anchor_patterns"]):
                is_supported_by_fresh_source = True

            # If no anchor, check if other non-stale-prone citations exist
            if not is_supported_by_fresh_source:
                # Check non-file citations
                if any(c is not FILE_PATH_PATTERN for c in citations_found):
                    is_supported_by_fresh_source = True

            # If still not supported, check mtime of file-path citations
            if not is_supported_by_fresh_source:
                cited_files = [mf.group(0) for mf in FILE_PATH_PATTERN.finditer(window)]
                now = dt.datetime.now().timestamp()
                threshold_seconds = rule["threshold_days"] * 24 * 60 * 60
                
                has_at_least_one_valid_file = False
                for file_path_str in cited_files:
                    is_stale_prone = any(p.search(file_path_str) for p in rule["stale_prone_patterns"])
                    
                    if not is_stale_prone:
                        has_at_least_one_valid_file = True
                        break

                    # It's a stale-prone file, check mtime
                    file_path = REPO / file_path_str
                    if not file_path.exists():
                        continue
                    
                    mtime = file_path.stat().st_mtime
                    if (now - mtime) <= threshold_seconds:
                        # It's stale-prone, but FRESH. Good enough.
                        has_at_least_one_valid_file = True
                        break
                
                if has_at_least_one_valid_file:
                    is_supported_by_fresh_source = True

            if not is_supported_by_fresh_source:
                findings.append(_build_finding("STALE_CITATION", detector, m, text))
    return findings


def _append_sentinel_report(findings: list[dict]) -> Path | None:
    """Append findings to today's claim-provenance sentinel report."""
    if not findings:
        return None
    SENTINEL_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    report_path = SENTINEL_DIR / f"claim-provenance-{today}.md"
    timestamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    session = os.environ.get("ECHO_SESSION", "unknown")

    new_file = not report_path.exists()
    with report_path.open("a", encoding="utf-8") as f:
        if new_file:
            f.write(f"# Claim Provenance Sentinel — {today}\n\n")
            f.write("_Generated by `.claude/hooks/claim-provenance-sentinel.py`._\n")
            f.write("_Tier: ADVISORY (7-day soak). Findings flag claims without "
                    "source citation (or with stale citations) within ±400 chars._\n\n")
            f.write("_Pair-rules: feedback_closing_claims_must_be_grep_derived.md, "
                    "feedback_brief_claims_must_be_api_or_grep_derived.md, "
                    "feedback_status_claims_must_be_grep_derived.md._\n\n")
        f.write(f"## Turn at {timestamp} (session {session})\n\n")
        f.write(f"| Finding Type | Detector | Kind | Match | Line | Context |\n")
        f.write(f"|---|---|---|---|---:|---|\n")
        for fnd in findings:
            ctx = fnd["context"].replace("|", "\\|")
            f.write(f"| {fnd['finding_type']} | `{fnd['detector_id']}` | {fnd['kind']} | `{fnd['match']}` | "
                    f"{fnd['line']} | {ctx} |\n")
        f.write("\n")
    return report_path


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "claim-provenance":
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)

    tier = os.environ.get("CLAIM_PROVENANCE_TIER", DEFAULT_TIER).lower()
    text = _read_transcript_last_assistant(data.get("transcript_path", ""))
    if not text:
        sys.exit(0)

    findings = _find_unsupported_claims(text)
    if not findings:
        sys.exit(0)

    report = _append_sentinel_report(findings)

    if tier == "blocking":
        msg_lines = [
            f"[CLAIM-PROVENANCE] {len(findings)} operational state claim(s) "
            f"lack co-located source citation or have stale citations.",
            "",
            "Each claim must include one of:",
            "  - `[source: <path>]` markdown footnote",
            "  - `[source: <mcp_tool> <timestamp>]` for API-derived claims",
            "  - Fresh file path (.md/.py/.yml) or strong anchor (active brief, today's date) cited within ~400 chars",
            "  - Echo internal ID (S###, D-YYYY-MMDD-###, AI-###-##, LAW N) "
            "or shown grep/git/MCP command",
            "",
            "Findings:",
        ]
        for f in findings[:10]:
            msg_lines.append(f"  - L{f['line']} [{f['finding_type']}/{f['detector_id']}] `{f['match']}`")
        if report:
            msg_lines += ["", f"Full report: {report.relative_to(REPO)}"]
        msg_lines += ["", "Override (this turn): SENTINEL_OVERRIDE=claim-provenance"]
        print(json.dumps({"decision": "block", "reason": "\n".join(msg_lines)}))
        sys.exit(0)

    # ADVISORY tier — log only, no block
    sys.exit(0)


if __name__ == "__main__":
    main()
