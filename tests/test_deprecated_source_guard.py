#!/usr/bin/env python3
"""Unit tests for deprecated-source-guard.py PreToolUse hook.

Run: python3 -m pytest tests/test_deprecated_source_guard.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "deprecated-source-guard.py"

# The hook reads $CLAUDE_PROJECT_DIR/docs/_active/source-registry.yml and logs
# to $CLAUDE_PROJECT_DIR/.claude/hooks/deprecated-read-log.json. The registry
# is project data, not part of this repo, so the tests build a throwaway
# project with a fixture registry instead of depending on one existing two
# directories above hooks/.
_PROJECT_TMP = tempfile.TemporaryDirectory(prefix="deprecated-source-guard-test-")
PROJECT = Path(_PROJECT_TMP.name)
LOG_PATH = PROJECT / ".claude" / "hooks" / "deprecated-read-log.json"

FIXTURE_REGISTRY = """\
version: 1
sources:
  memory/carryover-tracker.md:
    grade: DEPRECATED
    deprecated_since: 2026-04-14
    superseded_by:
      - memory/active-work.md
      - memory/backlog.md
    note: Split into active-work.md and backlog.md.
  memory/active-work.md:
    grade: CANONICAL
  memory/backlog.md:
    grade: CANONICAL
  memory/graveyard.md:
    grade: ADVISORY
"""


def setUpModule():
    registry = PROJECT / "docs" / "_active" / "source-registry.yml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(FIXTURE_REGISTRY, encoding="utf-8")


def tearDownModule():
    _PROJECT_TMP.cleanup()


def run_hook(payload: dict, extra_env: dict | None = None) -> tuple[int, str, str]:
    """Invoke the hook with a payload, return (rc, stdout, stderr)."""
    env = {**os.environ}
    env["CLAUDE_PROJECT_DIR"] = str(PROJECT)
    if extra_env:
        env.update(extra_env)
    # Clear any inherited reason var unless caller sets it
    if extra_env is None or "READ_DEPRECATED_REASON" not in extra_env:
        env.pop("READ_DEPRECATED_REASON", None)
    if extra_env is None or "SENTINEL_OVERRIDE" not in extra_env:
        env.pop("SENTINEL_OVERRIDE", None)
    result = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


class TestDeprecatedSourceGuard(unittest.TestCase):

    def test_deprecated_file_no_bypass_blocks(self):
        """Reading a DEPRECATED-graded file without bypass should return rc=2."""
        rc, _, err = run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": "memory/carryover-tracker.md"},
        })
        self.assertEqual(rc, 2)
        self.assertIn("DEPRECATED-SOURCE-GUARD", err)
        self.assertIn("memory/carryover-tracker.md", err)

    def test_block_message_names_replacements(self):
        """Block message must name the canonical replacements."""
        rc, _, err = run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": "memory/carryover-tracker.md"},
        })
        self.assertEqual(rc, 2)
        self.assertIn("memory/active-work.md", err)
        self.assertIn("memory/backlog.md", err)

    def test_block_message_names_deprecation_date(self):
        """Block message must include the deprecated_since date."""
        rc, _, err = run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": "memory/carryover-tracker.md"},
        })
        self.assertEqual(rc, 2)
        self.assertIn("2026-04-14", err)

    def test_block_message_shows_bypass_instructions(self):
        """Block message must explain the bypass mechanism."""
        rc, _, err = run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": "memory/carryover-tracker.md"},
        })
        self.assertEqual(rc, 2)
        self.assertIn("READ_DEPRECATED_REASON", err)
        self.assertIn("deprecated-read-log.json", err)

    def test_deprecated_with_reason_bypass_allows(self):
        """READ_DEPRECATED_REASON env should allow the read."""
        rc, _, _ = run_hook(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "memory/carryover-tracker.md"},
            },
            extra_env={"READ_DEPRECATED_REASON": "test bypass"},
        )
        self.assertEqual(rc, 0)

    def test_sentinel_override_allows(self):
        """SENTINEL_OVERRIDE=deprecated-source-guard should allow the read."""
        rc, _, _ = run_hook(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "memory/carryover-tracker.md"},
            },
            extra_env={"SENTINEL_OVERRIDE": "deprecated-source-guard"},
        )
        self.assertEqual(rc, 0)

    def test_canonical_file_allows(self):
        """Reading a CANONICAL-graded file should pass through."""
        rc, _, _ = run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": "memory/active-work.md"},
        })
        self.assertEqual(rc, 0)

    def test_advisory_file_allows(self):
        """Reading an ADVISORY-graded file should pass through."""
        rc, _, _ = run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": "memory/graveyard.md"},
        })
        self.assertEqual(rc, 0)

    def test_unknown_path_allows(self):
        """Path not present in registry should pass through (fail-open)."""
        rc, _, _ = run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": "memory/random-not-in-registry.md"},
        })
        self.assertEqual(rc, 0)

    def test_non_read_tool_ignored(self):
        """Edit/Write/Bash tool calls should be ignored even on DEPRECATED."""
        for tool in ("Edit", "Write", "Bash", "Grep"):
            rc, _, _ = run_hook({
                "tool_name": tool,
                "tool_input": {"file_path": "memory/carryover-tracker.md"},
            })
            self.assertEqual(rc, 0, f"Tool {tool} should pass through")

    def test_absolute_path_resolves(self):
        """Absolute paths should resolve to repo-relative and apply rule."""
        abs_path = str(PROJECT / "memory" / "carryover-tracker.md")
        rc, _, err = run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": abs_path},
        })
        self.assertEqual(rc, 2)
        self.assertIn("memory/carryover-tracker.md", err)

    def test_missing_file_path_ignored(self):
        """Payload without file_path should pass through cleanly."""
        rc, _, _ = run_hook({"tool_name": "Read", "tool_input": {}})
        self.assertEqual(rc, 0)

    def test_malformed_payload_ignored(self):
        """Malformed JSON or missing tool_name should not block."""
        result = subprocess.run(
            ["python3", str(HOOK)],
            input="not-valid-json",
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(PROJECT)},
        )
        self.assertEqual(result.returncode, 0)

    def test_path_outside_repo_ignored(self):
        """Paths outside the repo should pass through (no false-positive)."""
        rc, _, _ = run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/some-other-file.md"},
        })
        self.assertEqual(rc, 0)

    def test_log_written_on_block(self):
        """Block should append to deprecated-read-log.json."""
        log_path = LOG_PATH
        if log_path.exists():
            log_path.unlink()
        run_hook({
            "tool_name": "Read",
            "tool_input": {"file_path": "memory/carryover-tracker.md"},
        })
        self.assertTrue(log_path.exists())
        last_line = log_path.read_text().strip().split("\n")[-1]
        entry = json.loads(last_line)
        self.assertEqual(entry["action"], "blocked")
        self.assertEqual(entry["file"], "memory/carryover-tracker.md")

    def test_log_written_on_bypass(self):
        """Bypass should append to log with reason field."""
        log_path = LOG_PATH
        if log_path.exists():
            log_path.unlink()
        run_hook(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "memory/carryover-tracker.md"},
            },
            extra_env={"READ_DEPRECATED_REASON": "audit test"},
        )
        self.assertTrue(log_path.exists())
        last_line = log_path.read_text().strip().split("\n")[-1]
        entry = json.loads(last_line)
        self.assertEqual(entry["reason"], "audit test")
        self.assertEqual(entry["bypass_type"], "reason")


if __name__ == "__main__":
    unittest.main(verbosity=2)
