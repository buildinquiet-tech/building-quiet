#!/usr/bin/env python3
"""Unit tests for pbl-clone-default-sentinel.py.

Run: python3 -m unittest .claude/hooks/test_pbl_clone_default_sentinel.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta


HOOK_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "pbl-clone-default-sentinel.py",
)


def fire(payload: dict, env_override: str = "") -> tuple[int, str, str]:
    """Invoke the hook with payload on stdin. Returns (exit, stdout, stderr)."""
    env = os.environ.copy()
    if env_override:
        env["SENTINEL_OVERRIDE"] = env_override
    else:
        env.pop("SENTINEL_OVERRIDE", None)
    r = subprocess.run(
        ["python3", HOOK_PATH],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return r.returncode, r.stdout, r.stderr


def parse_decision(stdout: str) -> str:
    """Parse the hookSpecificOutput.permissionDecision from stdout JSON."""
    if not stdout.strip():
        return ""
    try:
        obj = json.loads(stdout)
        return obj.get("hookSpecificOutput", {}).get("permissionDecision", "")
    except json.JSONDecodeError:
        return ""


class TestPblCloneDefaultSentinel(unittest.TestCase):

    def test_01_non_edit_tool_passes(self):
        """Bash/Read/other tools should pass through without blocking."""
        rc, out, _ = fire({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_02_unwatched_path_passes(self):
        """Edit to a non-storyboard path should pass."""
        rc, out, _ = fire({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/foo.md", "new_string": "hi"},
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_03_storyboard_with_clone_source_passes(self):
        """Write to storyboard with clone_source in frontmatter passes."""
        content = (
            "---\n"
            "title: Test Reel\n"
            "clone_source: PBL §1 / @personalbrandlaunch0 (256K avg)\n"
            "---\n\n"
            "Body content."
        )
        rc, out, _ = fire({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/anyone/Documents/Echo/drafts/storyboard/new-reel.md",
                "content": content,
            },
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_04_storyboard_without_clone_source_blocks(self):
        """Write to NEW storyboard without clone_source should deny."""
        content = (
            "---\n"
            "title: Test Reel\n"
            "stage: storyboard\n"
            "---\n\n"
            "Body content."
        )
        rc, out, _ = fire({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/anyone/Documents/Echo/drafts/storyboard/new-reel.md",
                "content": content,
            },
        })
        self.assertEqual(rc, 0)  # Hook exits 0 but emits permissionDecision deny
        self.assertEqual(parse_decision(out), "deny")
        self.assertIn("LAW 19", out)
        self.assertIn("clone_source", out)

    def test_05_storyboard_no_frontmatter_blocks(self):
        """Write to storyboard without ANY frontmatter blocks."""
        content = "Just a body, no frontmatter."
        rc, out, _ = fire({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/anyone/Documents/Echo/drafts/storyboard/no-fm.md",
                "content": content,
            },
        })
        self.assertEqual(rc, 0)
        self.assertEqual(parse_decision(out), "deny")

    def test_06_novel_synthesis_clone_source_passes(self):
        """clone_source: NOVEL form is accepted per LAW 19.5."""
        content = (
            "---\n"
            'clone_source: NOVEL — synthesis_justification: original Substack\n'
            "---\n\nBody."
        )
        rc, out, _ = fire({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/anyone/Documents/Echo/drafts/storyboard/issue-01.md",
                "content": content,
            },
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_07_reels_path_also_watched(self):
        """drafts/reels/** is also LAW-19-scoped (parallel to storyboard)."""
        content = "---\ntitle: x\n---\nbody"  # no clone_source
        rc, out, _ = fire({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/anyone/Documents/Echo/drafts/reels/some-reel.md",
                "content": content,
            },
        })
        self.assertEqual(parse_decision(out), "deny")

    def test_08_override_env_passes(self):
        """SENTINEL_OVERRIDE=pbl-clone-default bypasses the gate."""
        content = "---\ntitle: x\n---\nbody"
        rc, out, _ = fire({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/anyone/Documents/Echo/drafts/storyboard/test.md",
                "content": content,
            },
        }, env_override="pbl-clone-default")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_09_legacy_file_carve_out(self):
        """Real file with mtime BEFORE 2026-05-09 is legacy-exempt."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False,
            dir=os.path.dirname(os.path.abspath(__file__)),
        ) as f:
            f.write("---\ntitle: legacy\n---\nbody")
            tmp_path = f.name
        try:
            # Set mtime to 2026-04-15 (pre-LAW-19)
            ts = datetime(2026, 4, 15).timestamp()
            os.utime(tmp_path, (ts, ts))

            # Rename to a storyboard-like path so it's watched
            storyboard_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", "drafts", "storyboard",
            )
            os.makedirs(storyboard_dir, exist_ok=True)
            target = os.path.join(storyboard_dir, "legacy-test.md")
            os.rename(tmp_path, target)
            os.utime(target, (ts, ts))

            rc, out, _ = fire({
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": target,
                    "old_string": "body",
                    "new_string": "new body",  # no clone_source
                },
            })
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "")
        finally:
            if os.path.exists(target):
                os.remove(target)

    def test_10_edit_adding_clone_source_passes(self):
        """Edit that ADDS clone_source in new_string passes."""
        rc, out, _ = fire({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/Users/anyone/Documents/Echo/drafts/storyboard/needs-update.md",
                "old_string": "title: x",
                "new_string": "title: x\nclone_source: PBL §6.2 / @creator",
            },
        })
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")

    def test_11_empty_stdin_passes(self):
        """Empty/malformed stdin exits cleanly."""
        rc, _, _ = fire({})
        self.assertEqual(rc, 0)

    def test_12_clone_source_outside_frontmatter_blocks(self):
        """clone_source must be IN frontmatter, not body."""
        content = (
            "---\n"
            "title: x\n"
            "---\n\n"
            "clone_source: PBL §1 (this is in body, not frontmatter)\n"
        )
        rc, out, _ = fire({
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/anyone/Documents/Echo/drafts/storyboard/wrong-place.md",
                "content": content,
            },
        })
        self.assertEqual(parse_decision(out), "deny")


if __name__ == "__main__":
    unittest.main()
