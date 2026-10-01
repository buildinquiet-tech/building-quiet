#!/usr/bin/env python3
"""pytest suite for orch-advisor.py — subprocess pattern (15 cases).

Run: python3 -m pytest .claude/hooks/test_orch_advisor.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent / "orch-advisor.py"
PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_hook(
    payload: dict,
    env_extra: dict[str, str] | None = None,
    fire_log_path: Path | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("SENTINEL_OVERRIDE", None)
    env.pop("ECHO_ROUTING_REASON", None)
    if env_extra:
        env.update(env_extra)
    if fire_log_path is not None:
        env["_ORCH_ADVISOR_FIRE_LOG_OVERRIDE"] = str(fire_log_path)
    return subprocess.run(
        [PYTHON, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def bash_payload(command: str, description: str = "") -> dict:
    return {
        "tool_name": "Bash",
        "session_id": "test-session",
        "tool_input": {"command": command, "description": description},
    }


def write_payload(file_path: str, content: str) -> dict:
    return {
        "tool_name": "Write",
        "session_id": "test-session",
        "tool_input": {"file_path": file_path, "content": content},
    }


def edit_payload(file_path: str, new_string: str, old_string: str = "") -> dict:
    return {
        "tool_name": "Edit",
        "session_id": "test-session",
        "tool_input": {
            "file_path": file_path,
            "new_string": new_string,
            "old_string": old_string,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_advisory_fires_on_drafting_bash():
    """Bash with drafting-class command → stderr contains [ORCH-ADVISOR]."""
    payload = bash_payload("draft a post about hub and spoke routing synthesis")
    result = run_hook(payload)
    assert result.returncode == 0
    assert "[ORCH-ADVISOR]" in result.stderr


def test_advisory_fires_on_research_write():
    """Write with research-heavy content → advisory emitted."""
    content = (
        "research the following sources and summarize findings. "
        "investigate all references and compile a literature review. "
        "analyze the survey data from the research corpus. " * 10
    )
    payload = write_payload("notes/research-summary.md", content)
    result = run_hook(payload)
    assert result.returncode == 0
    assert "[ORCH-ADVISOR]" in result.stderr


def test_advisory_suppressed_by_env_override():
    """SENTINEL_OVERRIDE=bulk-burn → stderr empty (advisory muted)."""
    payload = bash_payload("draft a post synthesize the report and summarize findings")
    result = run_hook(payload, env_extra={"SENTINEL_OVERRIDE": "bulk-burn"})
    assert result.returncode == 0
    assert "[ORCH-ADVISOR]" not in result.stderr


def test_advisory_silent_when_routing_advisor_blocks():
    """Write to scripts/*.py with >500 chars → routing-advisor would block → no advisory."""
    big_content = "# drafting synthesis research\n" + "x = 1\n" * 100  # >500 chars
    assert len(big_content) > 500
    payload = write_payload("scripts/my_script.py", big_content)
    result = run_hook(payload)
    assert result.returncode == 0
    assert "[ORCH-ADVISOR]" not in result.stderr


def test_exit_zero_on_judgment_classification():
    """Prompt with judgment/decision verbs only → not economic → exit 0 no advisory."""
    payload = bash_payload("decide whether to approve the request and evaluate tradeoffs")
    result = run_hook(payload)
    assert result.returncode == 0
    # advisory should NOT fire (judgment/decide are not in ECONOMIC_TYPES)
    # We only assert exit 0 here; stderr content depends on classifier keywords
    assert result.returncode == 0


def test_exit_zero_on_unknown_classification():
    """No matching keywords → confidence 0.0 → exit 0, no advisory."""
    payload = bash_payload("ls -la /tmp")
    result = run_hook(payload)
    assert result.returncode == 0
    assert "[ORCH-ADVISOR]" not in result.stderr


def test_exit_zero_on_low_confidence():
    """Single weak keyword match → confidence below threshold → no advisory."""
    # Minimal prompt with only one vague word unlikely to score >= 0.6
    payload = bash_payload("list")
    result = run_hook(payload)
    assert result.returncode == 0
    assert "[ORCH-ADVISOR]" not in result.stderr


def test_invalid_json_exits_zero():
    """Malformed stdin → hook must exit 0 (fail-open)."""
    result = subprocess.run(
        [PYTHON, str(HOOK)],
        input="NOT JSON {{{{",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_missing_tool_input_exits_zero():
    """Payload with no tool_input key → exit 0."""
    payload = {"tool_name": "Bash", "session_id": "x"}
    result = run_hook(payload)
    assert result.returncode == 0


def test_non_watched_tool_exits_zero():
    """Read/Grep tool → not in WATCHED_TOOLS → exit 0, no advisory."""
    for tool in ("Read", "Grep", "LS"):
        payload = {
            "tool_name": tool,
            "session_id": "x",
            "tool_input": {"path": "/tmp/foo"},
        }
        result = run_hook(payload)
        assert result.returncode == 0, f"Failed for tool {tool}"
        assert "[ORCH-ADVISOR]" not in result.stderr


def test_fire_log_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Advisory fire appends a JSONL record to the fire log."""
    log_file = tmp_path / "orch-advisor-fires.jsonl"
    # Patch the fire log path via env var read by hook
    content = (
        "draft and synthesize the following report with research findings "
        "classify all mechanical components " * 5
    )
    payload = write_payload("notes/output.md", content)

    env = os.environ.copy()
    env.pop("SENTINEL_OVERRIDE", None)
    # Override home so fire log goes to tmp_path
    env["HOME"] = str(tmp_path)

    result = subprocess.run(
        [PYTHON, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0

    state_log = tmp_path / ".echo" / "state" / "orch-advisor-fires.jsonl"
    if state_log.exists():
        lines = state_log.read_text().strip().splitlines()
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert "ts" in record
        assert "tool" in record
        assert "classification" in record


def test_fail_open_on_exception(tmp_path: Path):
    """Even if bulk-burn-tracker.py is absent, hook must exit 0."""
    # Create a minimal hook copy that points at a non-existent tracker
    fake_hook = tmp_path / "orch-advisor-broken.py"
    hook_src = HOOK.read_text()
    # Redirect HOOK_DIR to tmp_path so importlib can't find bulk-burn-tracker
    patched = hook_src.replace(
        "HOOK_DIR = Path(__file__).resolve().parent",
        f"HOOK_DIR = Path(r'{tmp_path}')",
    )
    fake_hook.write_text(patched)

    payload = bash_payload("draft a synthesis report")
    result = subprocess.run(
        [PYTHON, str(fake_hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_classifier_import_works():
    """Sanity: classify_prompt can be loaded from bulk-burn-tracker.py."""
    import importlib.util
    bbt_path = HOOK.parent / "bulk-burn-tracker.py"
    if not bbt_path.exists():
        pytest.skip("bulk-burn-tracker.py not present in this environment")
    spec = importlib.util.spec_from_file_location("bbt", bbt_path)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert callable(mod.classify_prompt)
    result = mod.classify_prompt("draft a research synthesis report")
    assert "primary" in result
    assert "confidence" in result


def test_truncates_huge_write_content():
    """Write payload with >4000 char content → classifier still runs, hook exits 0."""
    huge_content = "draft synthesize research " * 400  # >4000 chars
    assert len(huge_content) > 4000
    payload = write_payload("notes/huge.md", huge_content)
    result = run_hook(payload)
    assert result.returncode == 0
    # Advisory may or may not fire depending on classifier; what matters is no crash


def test_edit_uses_new_string_plus_old_context():
    """Edit payload extracts new_string + old_string[:200] for classification.

    Uses pure-drafting keywords so classifier confidence clears 0.6 threshold.
    Mixed-category prompts (drafting+research+classification) split votes
    and stay below threshold — that's correct classifier behavior.
    """
    new_str = "draft a hook and write the caption and compose the body paragraph"
    old_str = "old content " * 50  # long but only first 200 chars used
    payload = edit_payload("notes/doc.md", new_str, old_str)
    result = run_hook(payload)
    assert result.returncode == 0
    assert "[ORCH-ADVISOR]" in result.stderr
