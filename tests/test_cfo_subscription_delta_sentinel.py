#!/usr/bin/env python3
"""Unit tests for cfo-subscription-delta-sentinel.py PreToolUse hook."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

HOOK_PATH = Path(__file__).resolve().parent / "cfo-subscription-delta-sentinel.py"
TIMEZONE = ZoneInfo("America/Los_Angeles")


class TestCfoSubscriptionDeltaSentinel(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name)
        self.cfo_dir = self.project_root / "echo" / "cfo"
        self.cfo_dir.mkdir(parents=True, exist_ok=True)
        self.hooks_dir = self.project_root / ".claude" / "hooks"
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        self.today = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        self.delta_scan_path = self.cfo_dir / f"delta-scan-{self.today}.md"
        self.log_path = self.hooks_dir / "cfo-delta-sentinel-log.jsonl"

        # Mock dashboard content
        self.dashboard_content = """
| Subscription | $/mo | Renewal |
|---|---|---|
| Service A | $10.00 | 2026-06-01 |
| Service B | $25.50 | 2026-07-15 |
| Service C | $5.00 | 2026-06-20 |
"""

    def tearDown(self):
        self.tempdir.cleanup()

    def _run_hook(self, tool_name: str, tool_input: dict, env_vars: dict | None = None) -> tuple[int, str]:
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(self.project_root)
        if env_vars:
            env.update(env_vars)

        tool_call = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=tool_call,
            capture_output=True,
            text=True,
            env=env,
        )
        return result.returncode, result.stderr

    def _read_last_log_entry(self) -> dict:
        """Reads the last JSON object from the JSONL log file."""
        with self.log_path.open('r', encoding='utf-8') as f:
            lines = f.readlines()
        self.assertTrue(lines, "Log file is empty")
        return json.loads(lines[-1])

    def _create_valid_scan_file(self):
        content = (
            "This is a valid delta scan. "
            "It references subscription-audit.md and decisions.md. "
            "It also checks against the CTO brief in cto-brief.md. "
            + "A" * 500
        )
        self.delta_scan_path.write_text(content)

    def test_pass_with_valid_delta_scan(self):
        self._create_valid_scan_file()
        rc, stderr = self._run_hook("Write", {"file_path": "echo/cfo/subscription-audit.md"})
        self.assertEqual(rc, 0)
        self.assertEqual(stderr, "")
        self.assertTrue(self.log_path.exists())
        log_content = self._read_last_log_entry()
        self.assertEqual(log_content["verdict"], "PASS")

    def test_fail_no_delta_scan_file(self):
        rc, stderr = self._run_hook("Write", {"file_path": "echo/cfo/2026-05-13-S347-audit.md"})
        self.assertEqual(rc, 2)
        self.assertIn("[CFO DELTA SCAN MISSING]", stderr)
        self.assertIn(f"echo/cfo/delta-scan-{self.today}.md", stderr)

    def test_fail_delta_scan_file_too_small(self):
        self.delta_scan_path.write_text("Too small.")
        rc, stderr = self._run_hook("Write", {"file_path": "echo/cfo/subscription-audit.md"})
        self.assertEqual(rc, 2)
        self.assertIn("[CFO DELTA SCAN MISSING]", stderr)
        log_content = self._read_last_log_entry()
        self.assertEqual(log_content["verdict"], "BLOCK")
        self.assertIn("file size < 500 bytes", log_content["reason"])

    def test_fail_delta_scan_missing_decisions_ref(self):
        content = "Valid size but missing the required reference. Has subscription-audit.md and NOW.md." + "A" * 500
        self.delta_scan_path.write_text(content)
        rc, stderr = self._run_hook("Write", {"file_path": "echo/cfo/subscription-audit.md"})
        self.assertEqual(rc, 2)
        log_content = self._read_last_log_entry()
        self.assertIn("missing reference: decisions.md", log_content["reason"])

    def test_fail_delta_scan_missing_brief_ref(self):
        content = "Valid size but missing brief. Has subscription-audit.md and decisions.md." + "A" * 500
        self.delta_scan_path.write_text(content)
        rc, stderr = self._run_hook("Write", {"file_path": "echo/cfo/subscription-audit.md"})
        self.assertEqual(rc, 2)
        log_content = self._read_last_log_entry()
        self.assertIn("missing reference to any active brief", log_content["reason"])

    def test_bypass_with_env_var(self):
        env = {"SUBSCRIPTION_DELTA_SCAN_REASON": "urgent operator request"}
        rc, stderr = self._run_hook("Write", {"file_path": "echo/cfo/subscription-audit.md"}, env_vars=env)
        self.assertEqual(rc, 0)
        self.assertEqual(stderr, "")
        log_content = self._read_last_log_entry()
        self.assertEqual(log_content["verdict"], "BYPASS")
        self.assertEqual(log_content["reason"], "urgent operator request")

    def test_non_trigger_other_tool(self):
        rc, stderr = self._run_hook("Bash", {"command": "ls"})
        self.assertEqual(rc, 0)
        self.assertFalse(self.log_path.exists())

    def test_non_trigger_other_file_path(self):
        rc, stderr = self._run_hook("Write", {"file_path": "memory/decisions.md", "content": "test"})
        self.assertEqual(rc, 0)
        self.assertFalse(self.log_path.exists())

    def test_non_trigger_cfo_non_audit_file(self):
        rc, stderr = self._run_hook("Write", {"file_path": "echo/cfo/legal-reference.md", "content": "test"})
        self.assertEqual(rc, 0)
        self.assertFalse(self.log_path.exists())

    def test_detection_path_heuristic(self):
        rc, stderr = self._run_hook("Write", {"file_path": "echo/cfo/2026-05-13-S347-subscription-audit.md"})
        self.assertEqual(rc, 2)
        self.assertTrue(self.log_path.exists())
        log_content = self._read_last_log_entry()
        self.assertTrue(log_content["target"].startswith("PATH:"))

    def test_detection_path_heuristic_absolute_path(self):
        abs_path = self.project_root / "echo" / "cfo" / "subscription-audit.md"
        rc, stderr = self._run_hook("Write", {"file_path": str(abs_path)})
        self.assertEqual(rc, 2)
        self.assertIn("[CFO DELTA SCAN MISSING]", stderr)
        log_content = self._read_last_log_entry()
        self.assertTrue(log_content["target"].startswith("PATH:"))
        self.assertIn("subscription-audit.md", log_content["target"])

    def test_detection_shape_heuristic(self):
        rc, stderr = self._run_hook("Write", {"file_path": "some/other/file.md", "content": self.dashboard_content})
        self.assertEqual(rc, 2)
        self.assertTrue(self.log_path.exists())
        log_content = self._read_last_log_entry()
        self.assertTrue(log_content["target"].startswith("SHAPE:"))
        self.assertRegex(log_content["target"], r"SHAPE:h\d+,r\d+")

    def test_non_detection_insufficient_shape(self):
        content = "A single mention of a subscription to Service A for $10.00/mo."
        rc, stderr = self._run_hook("Write", {"file_path": "random.md", "content": content})
        self.assertEqual(rc, 0)
        self.assertFalse(self.log_path.exists())

    def test_non_detection_generic_dollar_table(self):
        content = """
| Item | Cost | Notes |
|---|---|---|
| Team Lunch | $150.00 | Celebration |
| Software License | $499.00 | Annual renewal for team |
| Office Supplies | $75.50 | Stocking up |
"""
        rc, stderr = self._run_hook("Write", {"file_path": "team/expenses.md", "content": content})
        self.assertEqual(rc, 0)
        self.assertEqual(stderr, "")
        self.assertFalse(self.log_path.exists())

    def test_log_written_for_all_fires(self):
        # BLOCK
        self._run_hook("Write", {"file_path": "echo/cfo/subscription-audit.md"})
        self.assertTrue(self.log_path.exists())
        block_log = self._read_last_log_entry()
        self.assertEqual(block_log["verdict"], "BLOCK")
        self.log_path.unlink()

        # BYPASS
        env = {"SUBSCRIPTION_DELTA_SCAN_REASON": "test"}
        self._run_hook("Write", {"file_path": "echo/cfo/subscription-audit.md"}, env_vars=env)
        self.assertTrue(self.log_path.exists())
        bypass_log = self._read_last_log_entry()
        self.assertEqual(bypass_log["verdict"], "BYPASS")
        self.log_path.unlink()

        # PASS
        self._create_valid_scan_file()
        self._run_hook("Write", {"file_path": "echo/cfo/subscription-audit.md"})
        self.assertTrue(self.log_path.exists())
        pass_log = self._read_last_log_entry()
        self.assertEqual(pass_log["verdict"], "PASS")

    def test_pass_micro_edit_carve_out(self):
        tool_input = {
            "file_path": "echo/cfo/subscription-audit.md",
            "old_string": "Old line about a subscription.",
            "new_string": "New line about a subscription with a small typo fix."
        }
        rc, stderr = self._run_hook("Edit", tool_input)
        self.assertEqual(rc, 0)
        self.assertEqual(stderr, "")
        log_content = self._read_last_log_entry()
        self.assertEqual(log_content["verdict"], "PASS")
        self.assertEqual(log_content["reason"], "micro-edit carve-out")


if __name__ == "__main__":
    unittest.main(verbosity=2)
