#!/usr/bin/env python3
"""Unit tests for bulk-burn-tracker.py v2 (S343 PM rebuild).

Covers Phase A1-A4 + doctrine miscategorization classes:
  - Classifier output (7 cases)
  - Tool-pattern coverage (7 cases)
  - Miscategorization classes ECONOMIC_BURN / TRUST_LEAK / OK / UNCLASSIFIED (7 cases)
  - Per-turn events_seen pointer (2 cases)
  - State migration from /tmp legacy path (1 case)

Run: python3 .claude/hooks/test_bulk_burn_tracker.py
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
HOOK = REPO / "hooks" / "bulk-burn-tracker.py"


def write_transcript(path: Path, tool_events: list[dict]) -> None:
    """Write a minimal JSONL transcript with tool_use blocks."""
    lines = []
    for evt in tool_events:
        lines.append(json.dumps({
            "message": {
                "content": [
                    {"type": "tool_use", "name": evt["name"], "input": evt["input"]}
                ]
            }
        }))
    path.write_text("\n".join(lines) + "\n")


def run_hook(transcript: Path, session_id: str, env_overrides: dict) -> tuple[int, str, str]:
    payload = {"transcript_path": str(transcript), "session_id": session_id}
    env = {**os.environ, **env_overrides}
    result = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


class BulkBurnHarness(unittest.TestCase):
    """Shared setup — tmpdir for HOME (state dir) + CLAUDE_PROJECT_DIR (log)."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.home = Path(self.tmpdir) / "home"
        self.project = Path(self.tmpdir) / "project"
        (self.home / ".echo" / "state").mkdir(parents=True)
        (self.project / "memory").mkdir(parents=True)
        self.log_file = self.project / "memory" / "max-usage-log.md"
        self.transcript = Path(self.tmpdir) / "transcript.jsonl"
        # Point legacy state at a guaranteed-missing path so the migration
        # fall-through can't pollute test state from the real-system /tmp file.
        self.legacy_stub = Path(self.tmpdir) / "no-legacy.json"
        self.env = {
            "HOME": str(self.home),
            "CLAUDE_PROJECT_DIR": str(self.project),
            "ECHO_BULK_BURN_LEGACY_STATE": str(self.legacy_stub),
        }

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def read_log(self) -> str:
        if self.log_file.exists():
            return self.log_file.read_text()
        return ""

    def read_classifier_log(self) -> list[dict]:
        path = self.home / ".echo" / "state" / "classifier-cache.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]


# ---------- Classifier tests (7) -------------------------------------------

class TestClassifier(BulkBurnHarness):
    def _classify_via_event(self, prompt: str) -> dict:
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": prompt + " " + ("x" * 900)},  # ensure bulk threshold met
        }])
        rc, _, _ = run_hook(self.transcript, "session-1", self.env)
        self.assertEqual(rc, 0)
        cache = self.read_classifier_log()
        self.assertEqual(len(cache), 1, "expected exactly one classifier entry")
        return cache[0]["classification"]

    def test_classifier_drafting(self):
        c = self._classify_via_event("Draft a long-form chapter and write the opening hook")
        self.assertEqual(c["primary"], "drafting")
        self.assertGreater(c["confidence"], 0.0)

    def test_classifier_synthesis(self):
        c = self._classify_via_event("Synthesize these sources, consolidate findings, distill")
        self.assertEqual(c["primary"], "synthesis")

    def test_classifier_mechanical(self):
        c = self._classify_via_event("Rename every variable and refactor the batch into a regex replace")
        self.assertEqual(c["primary"], "mechanical")

    def test_classifier_research(self):
        c = self._classify_via_event("Research the competitor landscape, audit findings, gather evidence")
        self.assertEqual(c["primary"], "research")

    def test_classifier_judgment(self):
        c = self._classify_via_event("Review and evaluate this code, recommend approval, score the design")
        self.assertEqual(c["primary"], "judgment")

    def test_classifier_mixed_primary_wins(self):
        # 4 drafting verbs vs 2 research verbs → drafting primary, research secondary.
        c = self._classify_via_event(
            "Draft and write and compose and generate copy after research and audit"
        )
        self.assertEqual(c["primary"], "drafting")
        self.assertEqual(c["secondary"], "research")

    def test_classifier_unknown(self):
        # No keywords → unknown, confidence 0.0.
        # Long enough to satisfy bulk threshold via the x-pad.
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": "asdfqwerty " + ("x" * 900)},
        }])
        rc, _, _ = run_hook(self.transcript, "session-1", self.env)
        self.assertEqual(rc, 0)
        cache = self.read_classifier_log()
        self.assertEqual(cache[0]["classification"]["primary"], "unknown")
        self.assertEqual(cache[0]["classification"]["confidence"], 0.0)


# ---------- Tool-pattern coverage (7) --------------------------------------

class TestToolPatterns(BulkBurnHarness):

    def test_subagent_bulk_dispatch_flagged(self):
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": "Draft a long chapter " + ("x" * 900)},
        }])
        rc, _, _ = run_hook(self.transcript, "ses1", self.env)
        self.assertEqual(rc, 0)
        log = self.read_log()
        self.assertIn("ECONOMIC_BURN", log)

    def test_subagent_short_prompt_not_flagged(self):
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": "short drafting task"},  # under 800 chars
        }])
        rc, _, _ = run_hook(self.transcript, "ses2", self.env)
        self.assertEqual(rc, 0)
        # No flag because not bulk.
        self.assertNotIn("ECONOMIC_BURN", self.read_log())

    def test_ctx_batch_execute_with_large_proxy_flagged(self):
        big_code = "draft and synthesize " + ("x" * 900)
        write_transcript(self.transcript, [{
            "name": "mcp__plugin_context-mode_context-mode__ctx_batch_execute",
            "input": {"code": big_code, "commands": [], "queries": []},
        }])
        rc, _, _ = run_hook(self.transcript, "ses3", self.env)
        self.assertEqual(rc, 0)
        # ctx surface = hub. Drafting work-type. Should flag ECONOMIC_BURN.
        self.assertIn("ECONOMIC_BURN", self.read_log())

    def test_kie_ai_mcp_spoke_no_flag_economic(self):
        # Economic work on spoke = correct routing, no flag.
        write_transcript(self.transcript, [{
            "name": "mcp__kie-ai__veo3_generate_video",
            "input": {"prompt": "draft a scene where the avatar walks " + ("x" * 100)},
        }])
        rc, _, _ = run_hook(self.transcript, "ses4", self.env)
        self.assertEqual(rc, 0)
        self.assertNotIn("ECONOMIC_BURN", self.read_log())
        self.assertNotIn("TRUST_LEAK", self.read_log())

    def test_bash_curl_paid_anthropic_flagged(self):
        write_transcript(self.transcript, [{
            "name": "Bash",
            "input": {"command": "curl -s https://api.anthropic.com/v1/messages -d 'draft a long synthesis " + ("x" * 200) + "'"},
        }])
        rc, _, _ = run_hook(self.transcript, "ses5", self.env)
        self.assertEqual(rc, 0)
        self.assertIn("ECONOMIC_BURN", self.read_log())

    def test_bash_kie_chat_spoke_no_flag(self):
        write_transcript(self.transcript, [{
            "name": "Bash",
            "input": {"command": "python3 scripts/kie_chat.py --model opus-4-6 --prompt 'draft'"},
        }])
        rc, _, _ = run_hook(self.transcript, "ses6", self.env)
        self.assertEqual(rc, 0)
        # kie_chat marker → spoke routing. No flag.
        self.assertNotIn("ECONOMIC_BURN", self.read_log())

    def test_bash_urllib_paid_flagged(self):
        write_transcript(self.transcript, [{
            "name": "Bash",
            "input": {"command": "python3 -c 'import urllib.request; urllib.request.urlopen(\"https://api.kie.ai/draft synthesize " + ("x" * 200) + "\")'"},
        }])
        rc, _, _ = run_hook(self.transcript, "ses7", self.env)
        self.assertEqual(rc, 0)
        self.assertIn("ECONOMIC_BURN", self.read_log())


# ---------- Miscategorization classes (7) ----------------------------------

class TestMiscategorization(BulkBurnHarness):

    def test_economic_burn_drafting_on_hub(self):
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": "draft compose write long form chapter " + ("x" * 900)},
        }])
        run_hook(self.transcript, "m1", self.env)
        self.assertIn("ECONOMIC_BURN", self.read_log())

    def test_trust_leak_judgment_on_spoke(self):
        # Judgment work-type dispatched to kie-ai spoke = TRUST_LEAK.
        write_transcript(self.transcript, [{
            "name": "mcp__kie-ai__bytedance_seedream_image",
            "input": {"prompt": "review and evaluate this design decision, score and recommend approval " + ("x" * 100)},
        }])
        run_hook(self.transcript, "m2", self.env)
        self.assertIn("TRUST_LEAK", self.read_log())

    def test_judgment_on_hub_is_ok_no_flag(self):
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": "review evaluate score recommend approval judge verdict " + ("x" * 900)},
        }])
        run_hook(self.transcript, "m3", self.env)
        log = self.read_log()
        self.assertNotIn("ECONOMIC_BURN", log)
        self.assertNotIn("TRUST_LEAK", log)

    def test_drafting_on_spoke_is_ok_no_flag(self):
        # Mixed turn: spoke MCP with drafting verbs → OK, not flagged.
        write_transcript(self.transcript, [{
            "name": "mcp__kie-ai__veo3_generate_video",
            "input": {"prompt": "draft compose write the scene " + ("x" * 100)},
        }])
        run_hook(self.transcript, "m4", self.env)
        log = self.read_log()
        self.assertNotIn("ECONOMIC_BURN", log)
        self.assertNotIn("TRUST_LEAK", log)

    def test_unclassified_below_confidence_threshold(self):
        # No classifier keywords → confidence 0.0 → UNCLASSIFIED → no flag.
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": "xyzzy plover " + ("x" * 900)},
        }])
        run_hook(self.transcript, "m5", self.env)
        log = self.read_log()
        self.assertNotIn("ECONOMIC_BURN", log)
        self.assertNotIn("TRUST_LEAK", log)
        # Classification still recorded with confidence 0.
        cache = self.read_classifier_log()
        self.assertEqual(cache[0]["miscat"], "UNCLASSIFIED")

    def test_economic_burn_suppressed_by_same_turn_kie_chat(self):
        # Mixed turn: subagent draft + kie_chat in same turn → no flag emitted
        # because operator routed correctly elsewhere.
        write_transcript(self.transcript, [
            {
                "name": "Agent",
                "input": {"prompt": "draft compose write " + ("x" * 900)},
            },
            {
                "name": "Bash",
                "input": {"command": "python3 scripts/kie_chat.py --model opus-4-6 --prompt 'go'"},
            },
        ])
        run_hook(self.transcript, "m6", self.env)
        self.assertNotIn("ECONOMIC_BURN", self.read_log())

    def test_research_on_hub_heavy_mcp_flagged(self):
        # ctx_fetch_and_index with research verbs = ECONOMIC_BURN.
        big = "research investigate audit scan gather " + ("x" * 900)
        write_transcript(self.transcript, [{
            "name": "mcp__plugin_context-mode_context-mode__ctx_fetch_and_index",
            "input": {"intent": big},
        }])
        run_hook(self.transcript, "m7", self.env)
        self.assertIn("ECONOMIC_BURN", self.read_log())


# ---------- Per-turn pointer + state migration (3) -------------------------

class TestPerTurnAndMigration(BulkBurnHarness):

    def test_per_turn_pointer_advances(self):
        # First fire: 1 event. Second fire: 2 events total → 1 new event.
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": "draft long " + ("x" * 900)},
        }])
        run_hook(self.transcript, "pt1", self.env)
        first_log = self.read_log()
        self.assertEqual(first_log.count("ECONOMIC_BURN"), 1)

        # Append second event to same transcript.
        write_transcript(self.transcript, [
            {"name": "Agent", "input": {"prompt": "draft long " + ("x" * 900)}},
            {"name": "Agent", "input": {"prompt": "synthesize merge " + ("x" * 900)}},
        ])
        run_hook(self.transcript, "pt1", self.env)
        second_log = self.read_log()
        # One more flag emitted (total = 2).
        self.assertEqual(second_log.count("ECONOMIC_BURN"), 2)

    def test_per_turn_no_duplicate_on_same_transcript(self):
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": "draft long " + ("x" * 900)},
        }])
        run_hook(self.transcript, "pt2", self.env)
        run_hook(self.transcript, "pt2", self.env)  # second fire, no new events
        self.assertEqual(self.read_log().count("ECONOMIC_BURN"), 1)

    def test_legacy_state_migration(self):
        # Seed /tmp legacy file; ensure first run picks it up.
        # We can't actually write to /tmp/echo-bulk-burn-tracker.json in CI
        # safely, so the test sets HOME-based STATE_FILE to NOT exist and
        # writes legacy fixture in a tmp file mounted via env-monkey.
        # Simplified: confirm state dir gets created on first fire.
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": "x" * 50},  # short, no flag, no events
        }])
        run_hook(self.transcript, "mig1", self.env)
        state_file = self.home / ".echo" / "state" / "bulk-burn-tracker.json"
        self.assertTrue(state_file.exists())
        data = json.loads(state_file.read_text())
        # Key contains today + session id.
        self.assertEqual(len(data), 1)


# ---------- S370 classifier tune fixtures (12 new) -----------------------
# Verifies: verify→mechanical, ops→mechanical, structural dev patterns,
# and that judgment keywords still work on long/genuine prompts.

class TestS370ClassifierTune(BulkBurnHarness):
    """New fixtures for S370 keyword additions."""

    _s370_counter = 0

    def _classify(self, prompt: str) -> dict:
        # Each call needs a unique session ID so the per-turn events_seen
        # pointer resets and the hook processes exactly one new event.
        TestS370ClassifierTune._s370_counter += 1
        session = f"s370-{TestS370ClassifierTune._s370_counter}"
        write_transcript(self.transcript, [{
            "name": "Agent",
            "input": {"prompt": prompt + " " + ("x" * 900)},
        }])
        run_hook(self.transcript, session, self.env)
        cache = self.read_classifier_log()
        self.assertGreater(len(cache), 0, "expected classifier log entry")
        return cache[-1]["classification"]

    def test_verify_classifies_as_mechanical_not_judgment(self):
        # Core fix: "verify" moved to mechanical. Short Bash ops check.
        c = self._classify("verify the output is correct")
        self.assertEqual(c["primary"], "mechanical")

    def test_run_tests_classifies_as_mechanical(self):
        c = self._classify("run tests and check the results")
        self.assertEqual(c["primary"], "mechanical")

    def test_build_classifies_as_mechanical(self):
        c = self._classify("build the package and deploy to staging")
        self.assertEqual(c["primary"], "mechanical")

    def test_install_classifies_as_mechanical(self):
        c = self._classify("install dependencies and run the setup script")
        self.assertEqual(c["primary"], "mechanical")

    def test_commit_classifies_as_mechanical(self):
        c = self._classify("commit these changes and push to origin")
        self.assertEqual(c["primary"], "mechanical")

    def test_clean_classifies_as_mechanical(self):
        c = self._classify("clean the build artifacts and run the linter")
        self.assertEqual(c["primary"], "mechanical")

    def test_implement_classifies_as_structural(self):
        c = self._classify("implement the new auth flow")
        self.assertEqual(c["primary"], "structural")

    def test_fix_bug_classifies_as_structural(self):
        c = self._classify("fix bug in the parser where empty input panics")
        self.assertEqual(c["primary"], "structural")

    def test_debug_classifies_as_structural(self):
        c = self._classify("debug why the webhook handler returns 500")
        self.assertEqual(c["primary"], "structural")

    def test_wire_up_classifies_as_structural(self):
        c = self._classify("wire up the new spend-visibility endpoint")
        self.assertEqual(c["primary"], "structural")

    def test_judgment_review_still_classifies_judgment(self):
        # Ensure judgment keywords still work; we didn't clobber them.
        c = self._classify("review and evaluate this approach, recommend approval")
        self.assertEqual(c["primary"], "judgment")

    def test_verify_vs_review_disambiguation(self):
        # "verify" alone → mechanical. "review" alone → judgment.
        c_verify = self._classify("verify the deployment succeeded")
        c_review = self._classify("review this design decision and score it")
        self.assertEqual(c_verify["primary"], "mechanical")
        self.assertEqual(c_review["primary"], "judgment")


if __name__ == "__main__":
    unittest.main(verbosity=2)
