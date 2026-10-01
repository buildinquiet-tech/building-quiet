#!/usr/bin/env python3
"""Unit tests for closing-integrity-sentinel.py Stop hook.

Run: python3 .claude/hooks/test_closing_integrity_sentinel.py

Strategy: load the sentinel module via importlib (hyphen in filename),
monkey-patch `_import_snapshot` to return a stub helper, and exercise
`audit_block` + companion functions directly. A few integration tests
shell out to the hook via subprocess to cover the main() top-level flow.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "closing-integrity-sentinel.py"


def _load_sentinel():
    """Load the hook as a python module despite the hyphen in filename."""
    spec = importlib.util.spec_from_file_location("closing_integrity_sentinel", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_stub_snap(
    save_state_count: int = 0,
    head_sha: str = "abcdef1",
    branches: list[str] | None = None,
    quality_gate: dict | None = None,
    time_estimate: dict | None = None,
    library: dict | None = None,
):
    """Build a SimpleNamespace shaped like closing_state_snapshot."""
    return SimpleNamespace(
        save_state_count=lambda _handoff: save_state_count,
        git_state=lambda: {"head_sha": head_sha, "branches": branches or ["main"]},
        quality_gate_7d=lambda: quality_gate or {"violations": 0, "entries": 0},
        time_estimate_7d=lambda: time_estimate or {"miss_rate_pct": 0.0},
        library_state=lambda: library or {"total_files": 1193},
    )


def _write_handoff(tmpdir: Path, body: str) -> Path:
    handoff_dir = tmpdir / "echo" / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    handoff = handoff_dir / f"{dt.date.today().isoformat()}.md"
    handoff.write_text(body, encoding="utf-8")
    return handoff


class TestCompanionFunctions(unittest.TestCase):
    """Direct tests on _extract_closing_block / _block_is_marker_only / _file_age_minutes."""

    def setUp(self):
        self.mod = _load_sentinel()

    def test_no_handoff_returns_none(self):
        bogus = Path("/tmp/nonexistent_handoff_12345.md")
        self.assertIsNone(self.mod._extract_closing_block(bogus))

    def test_no_closing_block_in_file(self):
        with tempfile.TemporaryDirectory() as td:
            handoff = _write_handoff(
                Path(td),
                "# Handoff\n\n## S100 — save-state\nWork happened.\n",
            )
            self.assertIsNone(self.mod._extract_closing_block(handoff))

    def test_closing_block_extracted(self):
        body = (
            "# Handoff\n\n"
            "## S100 — save-state\nBefore.\n\n"
            "## CLOSING — RUN AT 21:00 PT (Echo, S100)\n"
            "Three save-state blocks today.\n"
            "main HEAD = abc1234.\n"
        )
        with tempfile.TemporaryDirectory() as td:
            handoff = _write_handoff(Path(td), body)
            block = self.mod._extract_closing_block(handoff)
            self.assertIsNotNone(block)
            self.assertIn("CLOSING — RUN AT", block)
            self.assertIn("save-state blocks", block)

    def test_marker_only_block_detected(self):
        marker_body = (
            "## CLOSING — RUN AT 21:00 PT (Echo, S100)\n"
            "Day fully closed. /save-state is the right tool for any "
            "post-closing extension work."
        )
        self.assertTrue(self.mod._block_is_marker_only(marker_body))

    def test_content_block_not_marker_only(self):
        rich_body = (
            "## CLOSING — RUN AT 21:00 PT (Echo, S100)\n"
            "Four save-state blocks today. main HEAD = deadbee.\n"
            "Library: 1,193 files / 0 errors."
        )
        self.assertFalse(self.mod._block_is_marker_only(rich_body))

    def test_file_age_minutes_for_fresh_file(self):
        with tempfile.TemporaryDirectory() as td:
            handoff = _write_handoff(Path(td), "# fresh")
            age = self.mod._file_age_minutes(handoff)
            self.assertLess(age, 1.0)  # just written, must be < 1 min

    def test_file_age_minutes_for_old_file(self):
        with tempfile.TemporaryDirectory() as td:
            handoff = _write_handoff(Path(td), "# old")
            # Set mtime to 2 hours ago
            two_hours_ago = time.time() - 7200
            os.utime(handoff, (two_hours_ago, two_hours_ago))
            age = self.mod._file_age_minutes(handoff)
            self.assertGreater(age, 119.0)  # ~120 min


class TestAuditBlock(unittest.TestCase):
    """Test audit_block defect detection with mocked snapshot helper."""

    def setUp(self):
        self.mod = _load_sentinel()

    def _audit(self, block: str, snap_kwargs: dict | None = None) -> list[dict]:
        snap = _make_stub_snap(**(snap_kwargs or {}))
        with tempfile.TemporaryDirectory() as td:
            handoff = _write_handoff(Path(td), "fake handoff")
            # patch _import_snapshot + REPO so relative_to() works against tempdir
            with patch.object(self.mod, "_import_snapshot", return_value=snap), \
                 patch.object(self.mod, "REPO", Path(td)), \
                 patch("subprocess.run") as mock_run:
                mock_run.return_value = SimpleNamespace(
                    stdout="* main\n  remotes/origin/main\n", returncode=0
                )
                return self.mod.audit_block(block, handoff)

    def test_save_state_count_mismatch_flagged(self):
        block = (
            "## CLOSING — RUN AT 21:00 PT\n"
            "Three save-state blocks today. Good day.\n"
        )
        defects = self._audit(block, {"save_state_count": 5})
        # claimed=3, actual=5 → defect
        claims = [d["claim"] for d in defects]
        self.assertIn("save_state_count", claims)
        ss_defect = next(d for d in defects if d["claim"] == "save_state_count")
        self.assertEqual(ss_defect["claimed"], 3)
        self.assertEqual(ss_defect["actual"], 5)

    def test_save_state_count_match_no_defect(self):
        block = (
            "## CLOSING — RUN AT 21:00 PT\n"
            "Four save-state blocks today.\n"
        )
        defects = self._audit(block, {"save_state_count": 4})
        claims = [d["claim"] for d in defects]
        self.assertNotIn("save_state_count", claims)

    def test_head_sha_mismatch_flagged(self):
        block = (
            "## CLOSING — RUN AT 21:00 PT\n"
            "main HEAD = deadbee finalized clean.\n"
        )
        defects = self._audit(block, {"head_sha": "abc1234"})
        claims = [d["claim"] for d in defects]
        self.assertIn("main_head", claims)

    def test_head_sha_match_no_defect(self):
        block = (
            "## CLOSING — RUN AT 21:00 PT\n"
            "main HEAD = abc1234 finalized clean.\n"
        )
        defects = self._audit(block, {"head_sha": "abc1234"})
        claims = [d["claim"] for d in defects]
        self.assertNotIn("main_head", claims)

    def test_branch_fabrication_flagged(self):
        # block lists a branch that's not in `git branch -a` output
        block = (
            "## CLOSING — RUN AT 21:00 PT\n"
            "Branches in flight:\n"
            " - fake-branch-S999: 5 commits ahead of main\n"
        )
        defects = self._audit(block)
        claims = [d["claim"] for d in defects]
        self.assertIn("branch_exists", claims)

    def test_quality_gate_count_mismatch_flagged(self):
        block = (
            "## CLOSING — RUN AT 21:00 PT\n"
            "Quality gate: 0/0 quality-gate violations this week.\n"
        )
        defects = self._audit(
            block, {"quality_gate": {"violations": 3, "entries": 100}}
        )
        claims = [d["claim"] for d in defects]
        self.assertIn("quality_gate_7d", claims)

    def test_library_files_mismatch_flagged(self):
        block = (
            "## CLOSING — RUN AT 21:00 PT\n"
            "Library reindex complete: 1,193 files / 0 errors clean.\n"
        )
        defects = self._audit(block, {"library": {"total_files": 1200}})
        claims = [d["claim"] for d in defects]
        self.assertIn("library_files", claims)

    def test_no_claims_no_defects(self):
        """A closing block with no auto-checkable claims yields no defects."""
        block = (
            "## CLOSING — RUN AT 21:00 PT (Echo, S100)\n"
            "Wrapped the day cleanly. No specific metrics cited.\n"
        )
        defects = self._audit(block)
        self.assertEqual(defects, [])


class TestMainIntegration(unittest.TestCase):
    """Integration tests against the hook entrypoint via subprocess."""

    def _run_hook(self, env_extra: dict | None = None) -> tuple[int, str, str]:
        env = {**os.environ}
        if env_extra:
            env.update(env_extra)
        else:
            env.pop("SENTINEL_OVERRIDE", None)
        result = subprocess.run(
            ["python3", str(HOOK)],
            input="{}",
            capture_output=True,
            text=True,
            env=env,
        )
        return result.returncode, result.stdout, result.stderr

    def test_sentinel_override_skips(self):
        rc, _, _ = self._run_hook(env_extra={"SENTINEL_OVERRIDE": "closing-integrity"})
        self.assertEqual(rc, 0)

    def test_no_closing_block_exits_clean(self):
        """Against the real repo: today's handoff likely has no CLOSING block yet,
        so the hook should exit 0 silently."""
        rc, stdout, stderr = self._run_hook()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
