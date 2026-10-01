#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

# Define paths relative to the test script's location
REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude" / "hooks" / "storyboard-entry-sentinel.py"
LOG_FILE = REPO / ".claude" / "hooks" / "storyboard-entry-log.jsonl"


def run_hook(
    payload: dict, extra_env: dict | None = None, raw_input: str | None = None
) -> tuple[int, str, str]:
    """Runs the hook as a subprocess with the given payload and environment."""
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)

    # Isolate tests by unsetting bypass env vars unless explicitly provided
    if extra_env is None or "STORYBOARD_NOVEL_REASON" not in extra_env:
        env.pop("STORYBOARD_NOVEL_REASON", None)
    if extra_env is None or "SENTINEL_OVERRIDE" not in extra_env:
        env.pop("SENTINEL_OVERRIDE", None)

    stdin_data = raw_input if raw_input is not None else json.dumps(payload)
    p = subprocess.run(
        ["python3", str(HOOK)],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
    )
    return p.returncode, p.stdout, p.stderr


def _read_last_log() -> dict | None:
    """Reads and parses the last JSONL entry from the hook's log file."""
    if not LOG_FILE.exists():
        return None
    with open(LOG_FILE, "r") as f:
        lines = f.readlines()
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except (json.JSONDecodeError, IndexError):
        return None


class TestStoryboardEntrySentinel(unittest.TestCase):
    def setUp(self):
        """Ensure the log file is clean before tests that check it."""
        if LOG_FILE.exists():
            LOG_FILE.unlink()

    def _make_path(self, subdir: str) -> str:
        """Generates a unique markdown file path within a given subdirectory."""
        return f"{subdir}/test-sentinel-{uuid.uuid4().hex[:8]}.md"

    def test_valid_frontmatter_passes(self):
        content = """---
id: TEST-A
clone_source: PBL §1.4 / 8-18x save ratio
reference_assets:
  - echo/scrape/test.md
---

body
"""
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/scheduled"), "content": content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 0, stderr)

    def test_missing_clone_source_blocks(self):
        content = "---\nreference_assets: ['echo/scrape/test.md']\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/storyboard"), "content": content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 2)
        self.assertIn("clone_source", stderr)

    def test_empty_clone_source_blocks(self):
        content = "---\nclone_source: ''\nreference_assets: ['echo/scrape/test.md']\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/storyboard"), "content": content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 2)

    def test_novel_without_justification_blocks(self):
        content = "---\nclone_source: 'NOVEL — first of its kind'\nreference_assets: ['a']\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/scheduled"), "content": content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 2)
        self.assertIn("synthesis_justification", stderr)

    def test_novel_with_justification_passes(self):
        content = """---
clone_source: NOVEL
synthesis_justification: 'no PBL match exists for this format'
reference_assets: ['asset1.md']
---
body"""
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/storyboard"), "content": content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 0, stderr)

    def test_missing_reference_assets_blocks(self):
        content = "---\nclone_source: 'PBL §2.1'\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/scheduled"), "content": content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 2)
        self.assertIn("reference_assets", stderr)

    def test_empty_reference_assets_blocks(self):
        content = "---\nclone_source: 'PBL §2.1'\nreference_assets: []\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/scheduled"), "content": content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 2)

    def test_existing_file_passes(self):
        path_str = self._make_path("drafts/scheduled")
        tmp_file_path = REPO / path_str
        (REPO / "drafts" / "scheduled").mkdir(parents=True, exist_ok=True)
        tmp_file_path.touch()
        try:
            bad_content = "---\nclone_source: ''\n---\nbody"
            payload = {"tool_name": "Write", "tool_input": {"file_path": path_str, "content": bad_content}}
            rc, stdout, stderr = run_hook(payload)
            self.assertEqual(rc, 0, f"Hook should allow editing existing files. stderr: {stderr}")
        finally:
            if tmp_file_path.exists():
                tmp_file_path.unlink()

    def test_edit_tool_always_passes(self):
        bad_content = "---\nclone_source: ''\n---\nbody"
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": self._make_path("drafts/storyboard"), "content": bad_content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 0, stderr)

    def test_wrong_path_scope_passes(self):
        bad_content = "---\nclone_source: ''\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "echo/handoffs/test-out-of-scope.md", "content": bad_content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 0, stderr)

    def test_archive_path_passes(self):
        bad_content = "---\nclone_source: ''\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/archive"), "content": bad_content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 0, stderr)

    def test_bypass_reason_env_allows(self):
        bad_content = "---\nclone_source: ''\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/scheduled"), "content": bad_content},
        }
        env = {"STORYBOARD_NOVEL_REASON": "test bypass reason"}
        rc, stdout, stderr = run_hook(payload, extra_env=env)
        self.assertEqual(rc, 0, stderr)
        last_log = _read_last_log()
        self.assertIsNotNone(last_log)
        self.assertEqual(last_log.get("bypass_type"), "reason")
        self.assertEqual(last_log.get("reason"), "test bypass reason")

    def test_sentinel_override_allows(self):
        bad_content = "---\nclone_source: ''\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/storyboard"), "content": bad_content},
        }
        env = {"SENTINEL_OVERRIDE": "storyboard-entry-sentinel"}
        rc, stdout, stderr = run_hook(payload, extra_env=env)
        self.assertEqual(rc, 0, stderr)
        last_log = _read_last_log()
        self.assertIsNotNone(last_log)
        self.assertEqual(last_log.get("bypass_type"), "sentinel_override")

    def test_block_message_names_missing_fields(self):
        bad_content = "---\nid: some-id\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/scheduled"), "content": bad_content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 2)
        self.assertIn("clone_source", stderr)
        self.assertIn("reference_assets", stderr)

    def test_block_message_names_bypass_mechanism(self):
        bad_content = "---\nclone_source: ''\n---\nbody"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": self._make_path("drafts/scheduled"), "content": bad_content},
        }
        rc, stdout, stderr = run_hook(payload)
        self.assertEqual(rc, 2)
        self.assertIn("STORYBOARD_NOVEL_REASON", stderr)
        self.assertIn("SENTINEL_OVERRIDE", stderr)

    def test_malformed_payload_passes(self):
        rc, stdout, stderr = run_hook(payload={}, raw_input="not-valid-json")
        self.assertEqual(rc, 0, f"Hook should fail open on malformed JSON. stderr: {stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
