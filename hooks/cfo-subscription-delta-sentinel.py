#!/usr/bin/env python3
"""CFO Subscription Delta Sentinel — PreToolUse hook.

Spec: S347, 2026-05-13
Tier: judgment (v1) -> hard (after 7-day soak)
Zone: A (operator approval pre-install)
Pair-rules:
  - feedback_canon_grep_before_external_audit.md (S325)
  - feedback_closing_claims_must_be_grep_derived.md (S341)
  - feedback_brief_claims_must_be_api_or_grep_derived.md (S342)
  - feedback_status_claims_must_be_grep_derived.md (S342)

Purpose:
Make it physically impossible to render a /cfo subscription dashboard
without first running the Subscription Delta Scan that the CFO skill
marks as "Mandatory first step." Closes the failure class of skipping
mandatory pre-flight SOPs documented in S347.

Trigger: PreToolUse hook on `Edit` or `Write` tools.
Behavior:
  1. Detects if the tool call is a CFO dashboard render via path and
     output-shape heuristics.
  2. Checks for `echo/cfo/delta-scan-{YYYY-MM-DD}.md` for today's date
     (America/Los_Angeles timezone).
  3. Validates the scan is substantive (>500 bytes, contains required
     source references).
  4. On FAIL: blocks tool call (exit 2) with an instructive message.
  5. On PASS: allows tool call through silently (exit 0).
  6. Logs all fires (PASS, BLOCK, BYPASS) to a JSONL file.
  7. Bypass via env var `SUBSCRIPTION_DELTA_SCAN_REASON="<reason>"`.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# --- Constants ---
PROJECT_ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[2]))
LOG_FILE = PROJECT_ROOT / ".claude" / "hooks" / "cfo-delta-sentinel-log.jsonl"
REQUIRED_SCAN_REFERENCES = ["subscription-audit.md", "decisions.md"]
REQUIRED_BRIEF_REFERENCES = ["cfo-brief.md", "cto-brief.md", "NOW.md"]
MIN_SCAN_FILE_SIZE_BYTES = 500
MICRO_EDIT_THRESHOLD_CHARS = 100
TIMEZONE = ZoneInfo("America/Los_Angeles")


def get_block_message(today_str: str) -> str:
    return f"""
[CFO DELTA SCAN MISSING] Per CFO skill 'Mandatory first step' (S258):
Subscription dashboard render blocked — no delta-scan artifact for today.

To unblock:
1. Run the Subscription Delta Scan per `.claude/skills/cfo/SKILL.md`
2. Write findings to: echo/cfo/delta-scan-{today_str}.md
3. File must reference: subscription-audit.md, decisions.md, AND one active brief
4. Retry the dashboard render

To bypass (operator-authorized only, logged):
SUBSCRIPTION_DELTA_SCAN_REASON="<reason>" <retry command>

Skip pattern audit: this session's full failure log lives in echo/handoffs/2026-05-13.md.
""".strip()


def detect_cfo_dashboard_render(tool_input: dict[str, str]) -> str | None:
    """Detect CFO dashboard render via path or content shape heuristics."""
    # Path Heuristic
    file_path = tool_input.get("file_path", "")
    if re.search(r"echo/cfo/[^/]*-audit[^/]*\.md$", file_path):
        return f"PATH:{file_path}"

    # Output-Shape Heuristic
    content = tool_input.get("content", "") or tool_input.get("new_string", "")
    if not content:
        return None

    # Tighten heuristic: Require subscription-specific tokens first.
    if not re.search(r"(\$/mo|Renewal)", content, re.IGNORECASE):
        return None

    headers = {
        "Subscription", "Renewal", "Last Charge", "$/mo",
        "Active Subscriptions", "Subscriptions Audit"
    }
    header_pattern = re.compile(
        "|".join(re.escape(h) for h in headers), re.IGNORECASE
    )
    found_headers = len(set(header_pattern.findall(content)))

    # A subscription row looks like: `| service | $12.34 | date/status |`
    row_pattern = re.compile(r"^\|.*\|.*\$.*\|.*\|", re.MULTILINE)
    found_rows = len(row_pattern.findall(content))

    if found_headers >= 2 and found_rows >= 3:
        return f"SHAPE:h{found_headers},r{found_rows}"

    return None


def delta_scan_is_substantive(scan_path: Path) -> tuple[bool, str]:
    """Check if the delta scan file meets substantive criteria."""
    if scan_path.stat().st_size < MIN_SCAN_FILE_SIZE_BYTES:
        return False, f"file size < {MIN_SCAN_FILE_SIZE_BYTES} bytes"

    content = scan_path.read_text(encoding="utf-8")
    for ref in REQUIRED_SCAN_REFERENCES:
        if ref not in content:
            return False, f"missing reference: {ref}"

    if not any(brief in content for brief in REQUIRED_BRIEF_REFERENCES):
        return False, "missing reference to any active brief (cfo, cto, NOW)"

    return True, "valid"


def log_event(target: str, verdict: str, reason: str, tool_name: str) -> None:
    """Append a JSON event to the log file."""
    event = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session": os.environ.get("ECHO_SESSION", "unknown"),
        "tool": tool_name,
        "target": target,
        "verdict": verdict,
        "reason": reason,
    }
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        # Don't let logging failures block execution
        pass


def main() -> int:
    """Hook entry point."""
    try:
        tool_call = json.load(sys.stdin)
        tool_name = tool_call.get("tool_name")
        tool_input = tool_call.get("tool_input", {})
    except (json.JSONDecodeError, AttributeError):
        return 0

    if tool_name not in ("Edit", "Write"):
        return 0

    target = detect_cfo_dashboard_render(tool_input)
    if target is None:
        return 0

    # Carve-out for micro-edits (typo fixes, etc.)
    if tool_name == "Edit":
        old_str = tool_input.get("old_string", "")
        new_str = tool_input.get("new_string", "")
        if old_str and new_str and len(new_str) <= len(old_str) + MICRO_EDIT_THRESHOLD_CHARS:
            log_event(target, "PASS", "micro-edit carve-out", tool_name)
            return 0

    # Operator bypass
    bypass_reason = os.environ.get("SUBSCRIPTION_DELTA_SCAN_REASON", "").strip()
    if bypass_reason:
        log_event(target, "BYPASS", bypass_reason, tool_name)
        return 0

    # Main check: look for today's delta scan
    today_str = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    delta_path = PROJECT_ROOT / "echo" / "cfo" / f"delta-scan-{today_str}.md"

    if delta_path.exists():
        is_substantive, reason = delta_scan_is_substantive(delta_path)
        if is_substantive:
            log_event(target, "PASS", str(delta_path.relative_to(PROJECT_ROOT)), tool_name)
            return 0
        else:
            log_event(target, "BLOCK", f"delta-scan invalid: {reason}", tool_name)
            print(get_block_message(today_str), file=sys.stderr)
            return 2

    # FAIL: no delta scan file for today
    log_event(target, "BLOCK", "no delta-scan artifact for today", tool_name)
    print(get_block_message(today_str), file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Fail open if the hook itself crashes
        sys.exit(0)
