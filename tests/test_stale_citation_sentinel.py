#!/usr/bin/env python3
"""
Tests for stale-citation-sentinel.py.
Spec: docs/superpowers/specs/2026-05-01-stale-citation-sentinel-spec.md
Run: python3 .claude/hooks/test_stale_citation_sentinel.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "stale-citation-sentinel.py"


def run_hook(payload: dict, project_dir: str, env_override: dict = None) -> tuple[int, str]:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = project_dir
    if env_override:
        env.update(env_override)
    proc = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stderr


class TestStaleCitationSentinel(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project = Path(self.tmpdir)
        # Build a stub project tree
        (self.project / "memory").mkdir(parents=True)
        (self.project / "echo" / "decisions").mkdir(parents=True)
        (self.project / "echo" / "cfo").mkdir(parents=True)
        (self.project / "echo" / "scrape").mkdir(parents=True)
        (self.project / "docs" / "_active").mkdir(parents=True)
        (self.project / "drafts" / "scheduled").mkdir(parents=True)

        # Fresh file (today)
        self.fresh = self.project / "memory" / "active-work.md"
        self.fresh.write_text("# fresh")
        # Stale file (30 days old)
        self.stale = self.project / "memory" / "feedback_old.md"
        self.stale.write_text("# stale")
        old_ts = time.time() - (30 * 86400)
        os.utime(self.stale, (old_ts, old_ts))
        # Another stale file
        self.stale2 = self.project / "echo" / "cfo" / "subscription-audit.md"
        self.stale2.write_text("# stale cfo")
        os.utime(self.stale2, (old_ts, old_ts))
        # Third stale file
        self.stale3 = self.project / "docs" / "_active" / "old-routing.md"
        self.stale3.write_text("# stale routing")
        os.utime(self.stale3, (old_ts, old_ts))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # === PASS CASES ===

    def test_01_fresh_citation_passes(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.project / "memory" / "decisions.md"),
                "content": "Decision: per memory/active-work.md the next step is X.",
            },
        }
        code, _ = run_hook(payload, str(self.project))
        self.assertEqual(code, 0, "fresh citation should pass")

    def test_02_stale_citation_with_annotation_passes(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.project / "memory" / "decisions.md"),
                "content": (
                    "Decision: per memory/feedback_old.md the rule applies.\n"
                    "Verified S307 2026-05-01 via empirical UI check.\n"
                ),
            },
        }
        code, _ = run_hook(payload, str(self.project))
        self.assertEqual(code, 0, "annotated stale citation should pass")

    def test_03_non_decision_grade_target_passes(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.project / "drafts" / "scheduled" / "post.md"),
                "content": "References memory/feedback_old.md no annotation here.",
            },
        }
        code, _ = run_hook(payload, str(self.project))
        self.assertEqual(code, 0, "non-decision-grade path should pass regardless of staleness")

    def test_04_no_citations_passes(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.project / "memory" / "decisions.md"),
                "content": "Decision text with no memory file references at all.",
            },
        }
        code, _ = run_hook(payload, str(self.project))
        self.assertEqual(code, 0, "no citations = no scan = pass")

    def test_05_override_env_passes_and_logs(self):
        log_path = self.project / ".claude" / "hooks"
        log_path.mkdir(parents=True)
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.project / "memory" / "decisions.md"),
                "content": "Decision: per memory/feedback_old.md proceed.",
            },
            "turn_id": "T123",
        }
        code, _ = run_hook(payload, str(self.project), env_override={"SENTINEL_OVERRIDE": "stale-citation"})
        self.assertEqual(code, 0, "override env should pass")
        # Verify telemetry written
        tlog = log_path / "sentinel-override-log.json"
        self.assertTrue(tlog.exists(), "telemetry log should be written on override")
        data = json.loads(tlog.read_text())
        self.assertGreaterEqual(len(data), 1)
        self.assertEqual(data[-1]["override_reason"], "stale-citation")
        self.assertEqual(data[-1]["turn_id"], "T123")

    # === BLOCK CASES ===

    def test_06_stale_citation_no_annotation_blocks(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.project / "memory" / "decisions.md"),
                "content": "Decision: per memory/feedback_old.md the rule applies. Therefore proceed.",
            },
        }
        code, stderr = run_hook(payload, str(self.project))
        self.assertEqual(code, 2, "stale + no annotation should block (exit 2)")
        self.assertIn("STALE-CITATION CHECK", stderr)
        self.assertIn("memory/feedback_old.md", stderr)

    def test_07_multiple_stale_citations_all_listed(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.project / "memory" / "decisions.md"),
                "content": (
                    "Decision: per memory/feedback_old.md, echo/cfo/subscription-audit.md, "
                    "and docs/_active/old-routing.md proceed."
                ),
            },
        }
        code, stderr = run_hook(payload, str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("memory/feedback_old.md", stderr)
        self.assertIn("echo/cfo/subscription-audit.md", stderr)
        self.assertIn("docs/_active/old-routing.md", stderr)

    def test_08_new_verdict_file_blocks(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.project / "echo" / "decisions" / "2026-05-02-foo-verdict.md"),
                "content": "Verdict: anchored on echo/cfo/subscription-audit.md, recommend X.",
            },
        }
        code, stderr = run_hook(payload, str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("STALE-CITATION CHECK", stderr)

    # === EDGE CASES ===

    def test_09_nonexistent_citation_passes(self):
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.project / "memory" / "decisions.md"),
                "content": "Decision: per memory/never-existed.md proceed.",
            },
        }
        code, _ = run_hook(payload, str(self.project))
        self.assertEqual(code, 0, "nonexistent cited file should pass (no mtime to check)")

    def test_10_annotation_outside_window_blocks(self):
        # Annotation 10 lines before citation should NOT count
        lines = ["Verified S100 via empirical-check."] + [""] * 10 + [
            "Decision: per memory/feedback_old.md proceed."
        ]
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.project / "memory" / "decisions.md"),
                "content": "\n".join(lines),
            },
        }
        code, _ = run_hook(payload, str(self.project))
        self.assertEqual(code, 2, "annotation outside ±3 line window should still block")

    def test_11_edit_with_stale_in_new_string_blocks(self):
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.project / "memory" / "decisions.md"),
                "old_string": "old text",
                "new_string": "Updated decision: per memory/feedback_old.md proceed.",
            },
        }
        code, stderr = run_hook(payload, str(self.project))
        self.assertEqual(code, 2)
        self.assertIn("STALE-CITATION CHECK", stderr)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
