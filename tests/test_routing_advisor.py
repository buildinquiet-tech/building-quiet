#!/usr/bin/env python3
"""Unit tests for routing-advisor.py v0.2 (calibration: persist vs hub-drafting).

v0.1 coverage (24 tests) retained verbatim as a regression gate.
v0.2 adds: persist-provenance (operator messages + git HEAD), mid-session
single-use bypass, two-layer failure semantics, fingerprint internals.

Spec: docs/superpowers/specs/2026-05-20-routing-advisor-calibration.md
Run: python3 .claude/hooks/test_routing_advisor.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "routing-advisor.py"

# Import the hook module for direct unit testing of fingerprint internals.
_spec = importlib.util.spec_from_file_location("routing_advisor_mod", HOOK)
RA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RA)


def run_hook(payload: dict, env_overrides: dict | None = None,
             project_dir: str | None = None) -> tuple[int, str, str]:
    env = {**os.environ}
    if project_dir:
        env["CLAUDE_PROJECT_DIR"] = project_dir
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def write_payload(file_path: str, content: str, session_id: str = "test-sess") -> dict:
    return {
        "tool_name": "Write",
        "session_id": session_id,
        "tool_input": {"file_path": file_path, "content": content},
    }


def edit_payload(file_path: str, old_string: str, new_string: str,
                 session_id: str = "test-sess") -> dict:
    return {
        "tool_name": "Edit",
        "session_id": session_id,
        "tool_input": {
            "file_path": file_path,
            "old_string": old_string,
            "new_string": new_string,
        },
    }


# --- v0.2 shared fixtures ---------------------------------------------------

APPROVED_LINES = [
    "This is the operator-approved kinetic-text reel draft for campaign BQ-066.",
    "It was reviewed and signed off during the S368 audit by the operator.",
    "The routing-advisor hook should treat this write as a content persist.",
    "It is not fresh hub-drafting: the drafting cognition happened upstream.",
    "The approved text is recorded before this write call occurs in context.",
    "Persisting already-approved content must never be blocked by the gate.",
    "This paragraph exists to give the fingerprint enough words to shingle.",
    "Eight-word shingles need a reasonable amount of running prose to compare.",
]
# Multi-line so git grep has a 40-300 char line to anchor on.
APPROVED = "\n".join(APPROVED_LINES * 2)

FRESH = "\n".join([
    "A wholly fresh paragraph generated on the hub this very turn with no.",
    "prior appearance anywhere in the transcript or in any committed file.",
    "It represents genuine hub-drafting that the routing advisor must block.",
    "Padding sentence to clear the five hundred character size threshold here.",
    "More padding to ensure the write size heuristic trips and we reach B/C.",
    "Even more running prose so the fingerprint layer has words to work with.",
] * 3)


def _write_transcript(project: str, messages: list) -> str:
    """messages: list of (role, text). role in {user, assistant}."""
    path = Path(project) / "session.jsonl"
    with open(path, "w") as f:
        for role, text in messages:
            f.write(json.dumps({
                "type": role,
                "message": {"role": role,
                            "content": [{"type": "text", "text": text}]},
            }) + "\n")
    return str(path)


def _write_transcript_with_tool_result(project: str, payload_text: str) -> str:
    """A transcript whose only APPROVED-bearing entry is a user-role
    tool_result block — i.e. tool output, not genuine operator input."""
    path = Path(project) / "session_tr.jsonl"
    with open(path, "w") as f:
        f.write(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": payload_text},
            ]},
        }) + "\n")
    return str(path)


def _git(args: list, cwd: str) -> None:
    subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def _init_git_repo(path: str) -> None:
    _git(["init", "-q"], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "test"], path)


def _git_commit_all(path: str, msg: str = "c") -> None:
    _git(["add", "-A"], path)
    _git(["commit", "-q", "-m", msg], path)


# ===========================================================================
# v0.1 REGRESSION GATE — 24 tests, retained verbatim
# ===========================================================================

class HookHarness(unittest.TestCase):
    """Isolated PROJECT_DIR per test so bypass-log writes don't pollute repo."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.project = Path(self.tmpdir) / "echo-test"
        (self.project / ".claude" / "hooks").mkdir(parents=True)
        (self.project / "scripts").mkdir(parents=True)
        (self.project / "drafts").mkdir(parents=True)
        (self.project / "drafts" / "sub").mkdir(parents=True)
        (self.project / "memory").mkdir(parents=True)
        self.project_str = str(self.project)
        self.bypass_log = self.project / ".claude" / "hooks" / "routing-bypass-log.jsonl"
        self._tmp_bypass_files: list = []

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for p in self._tmp_bypass_files:
            try:
                os.remove(p)
            except OSError:
                pass


class ToolFilterTests(HookHarness):

    def test_bash_tool_passes(self):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo hi"},
        }
        rc, _, _ = run_hook(payload, project_dir=self.project_str)
        self.assertEqual(rc, 0)

    def test_read_tool_passes(self):
        payload = {
            "tool_name": "Read",
            "tool_input": {"file_path": "scripts/foo.py"},
        }
        rc, _, _ = run_hook(payload, project_dir=self.project_str)
        self.assertEqual(rc, 0)


class PathFilterTests(HookHarness):

    def test_watched_scripts_direct_child(self):
        path = str(self.project / "scripts" / "big.py")
        rc, _, stderr = run_hook(
            write_payload(path, "x" * 1000),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)
        self.assertIn("ROUTING-ADVISOR BLOCK", stderr)

    def test_watched_hooks_direct_child(self):
        path = str(self.project / ".claude" / "hooks" / "new_hook.py")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 1000),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)

    def test_drafts_direct_child(self):
        path = str(self.project / "drafts" / "doc.md")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 1000),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)

    def test_drafts_nested_child_blocked(self):
        path = str(self.project / "drafts" / "sub" / "doc.md")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 1000),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)

    def test_scripts_nested_subdir_passes(self):
        """scripts/*/foo.py (subdir) should NOT match scripts/*.py glob."""
        nested = self.project / "scripts" / "subdir"
        nested.mkdir(parents=True, exist_ok=True)
        path = str(nested / "foo.py")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 1000),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 0)

    def test_memory_not_watched(self):
        path = str(self.project / "memory" / "active-work.md")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 5000),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 0)

    def test_external_absolute_path_passes(self):
        rc, _, _ = run_hook(
            write_payload("/tmp/outside-project.py", "x" * 1000),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 0)


class SizeHeuristicTests(HookHarness):

    def test_write_under_threshold_passes(self):
        path = str(self.project / "scripts" / "small.py")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 400),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 0)

    def test_write_over_threshold_blocks(self):
        path = str(self.project / "scripts" / "big.py")
        rc, _, stderr = run_hook(
            write_payload(path, "x" * 600),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)
        self.assertIn("write_size_threshold", stderr)

    def test_edit_new_string_threshold_blocks(self):
        path = str(self.project / "scripts" / "edit_me.py")
        rc, _, stderr = run_hook(
            edit_payload(path, "old", "x" * 400),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)
        self.assertIn("edit_new_string_threshold", stderr)

    def test_edit_delta_threshold_blocks(self):
        path = str(self.project / "scripts" / "edit_me.py")
        # old=50, new=275 -> new<300 BUT delta=225>200 -> BLOCK via delta
        rc, _, stderr = run_hook(
            edit_payload(path, "x" * 50, "y" * 275),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)
        self.assertIn("edit_delta_threshold", stderr)

    def test_edit_small_passes(self):
        path = str(self.project / "scripts" / "edit_me.py")
        rc, _, _ = run_hook(
            edit_payload(path, "old", "new"),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 0)

    def test_edit_replace_similar_size_passes(self):
        """Replacing 250 chars with 280 chars: new<300 + delta=30 -> PASS."""
        path = str(self.project / "scripts" / "edit_me.py")
        rc, _, _ = run_hook(
            edit_payload(path, "x" * 250, "y" * 280),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 0)


class BypassTests(HookHarness):

    def test_bypass_env_var_set_passes(self):
        path = str(self.project / "scripts" / "big.py")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 1000),
            env_overrides={"ECHO_ROUTING_REASON": "integration density"},
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 0)

    def test_bypass_env_empty_blocks(self):
        path = str(self.project / "scripts" / "big.py")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 1000),
            env_overrides={"ECHO_ROUTING_REASON": ""},
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)

    def test_bypass_writes_jsonl_entry(self):
        path = str(self.project / "scripts" / "big.py")
        run_hook(
            write_payload(path, "x" * 1000, session_id="sess-abc"),
            env_overrides={"ECHO_ROUTING_REASON": "test-reason"},
            project_dir=self.project_str,
        )
        self.assertTrue(self.bypass_log.exists())
        lines = self.bypass_log.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["reason"], "test-reason")
        self.assertEqual(entry["session_id"], "sess-abc")
        self.assertEqual(entry["tool"], "Write")
        self.assertIn("scripts/big.py", entry["file_path"])

    def test_multiple_bypasses_append_jsonl(self):
        path = str(self.project / "scripts" / "big.py")
        for i in range(3):
            run_hook(
                write_payload(path, "x" * 1000, session_id=f"sess-{i}"),
                env_overrides={"ECHO_ROUTING_REASON": f"reason-{i}"},
                project_dir=self.project_str,
            )
        lines = self.bypass_log.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        for i, line in enumerate(lines):
            entry = json.loads(line)
            self.assertEqual(entry["reason"], f"reason-{i}")


class StructuredStderrTests(HookHarness):

    def test_block_message_has_structured_fields(self):
        path = str(self.project / "scripts" / "big.py")
        rc, _, stderr = run_hook(
            write_payload(path, "x" * 750),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)
        self.assertIn("classification: drafting-class", stderr)
        self.assertIn("trigger: write_size_threshold", stderr)
        self.assertIn("threshold: 500", stderr)
        self.assertIn("observed: 750", stderr)
        self.assertIn("feedback_hub_trust_spokes_economic.md", stderr)
        self.assertIn("kie_chat.py", stderr)
        self.assertIn("ECHO_ROUTING_REASON", stderr)


class FailOpenTests(HookHarness):

    def test_malformed_json_passes(self):
        result = subprocess.run(
            ["python3", str(HOOK)],
            input="not json at all {{{",
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": self.project_str},
        )
        self.assertEqual(result.returncode, 0)

    def test_missing_file_path_passes(self):
        payload = {"tool_name": "Write", "tool_input": {"content": "x" * 1000}}
        rc, _, _ = run_hook(payload, project_dir=self.project_str)
        self.assertEqual(rc, 0)

    def test_empty_payload_passes(self):
        rc, _, _ = run_hook({}, project_dir=self.project_str)
        self.assertEqual(rc, 0)


# ===========================================================================
# v0.2 — CANDIDATE B: persist-provenance
# ===========================================================================

class ProvenanceTests(HookHarness):

    def test_persist_from_operator_message_passes(self):
        """Approved text present in a genuine operator (user) message -> PASS."""
        tp = _write_transcript(self.project_str, [
            ("user", "here is the final approved draft to persist:\n" + APPROVED),
            ("assistant", "understood, writing the file now"),
        ])
        (self.project / "drafts" / "ideation").mkdir(parents=True, exist_ok=True)
        path = str(self.project / "drafts" / "ideation" / "bq066.md")
        payload = write_payload(path, APPROVED)
        payload["transcript_path"] = tp
        rc, _, _ = run_hook(payload, project_dir=self.project_str)
        self.assertEqual(rc, 0)
        entry = json.loads(self.bypass_log.read_text().splitlines()[-1])
        self.assertEqual(entry["mode"], "persist-provenance:operator-message")

    def test_assistant_authored_content_not_provenance(self):
        """SECURITY INVARIANT — content authored by the hub (assistant text
        OR assistant tool_use) is NEVER provenance. Closes the retry-hole:
        a blocked Write's tool_use input lands in the transcript, but a
        retry must still BLOCK."""
        tp = _write_transcript(self.project_str, [
            ("user", "draft something for me"),
            ("assistant", APPROVED),  # hub-authored — must not count
        ])
        path = str(self.project / "drafts" / "x.md")
        payload = write_payload(path, APPROVED)
        payload["transcript_path"] = tp
        rc, _, _ = run_hook(payload, project_dir=self.project_str)
        self.assertEqual(rc, 2)

    def test_tool_result_not_provenance(self):
        """Content arriving as a user-role tool_result block (tool output,
        e.g. Read of a /tmp file) is NOT provenance — closes the
        /tmp-Read decoy."""
        tp = _write_transcript_with_tool_result(self.project_str, APPROVED)
        path = str(self.project / "drafts" / "x.md")
        payload = write_payload(path, APPROVED)
        payload["transcript_path"] = tp
        rc, _, _ = run_hook(payload, project_dir=self.project_str)
        self.assertEqual(rc, 2)

    def test_persist_from_committed_blob_passes(self):
        """Approved text present in a git HEAD committed file -> PASS."""
        _init_git_repo(self.project_str)
        (self.project / "memory" / "handoff.md").write_text(
            "# Handoff\n\n" + APPROVED + "\n")
        _git_commit_all(self.project_str, "approved handoff")
        path = str(self.project / "drafts" / "persisted.md")
        rc, _, _ = run_hook(
            write_payload(path, APPROVED),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 0)
        entry = json.loads(self.bypass_log.read_text().splitlines()[-1])
        self.assertEqual(entry["mode"], "persist-provenance:git-head")

    def test_working_tree_only_file_does_not_pass(self):
        """Content in an UNCOMMITTED file only -> BLOCK (closes decoy hole)."""
        _init_git_repo(self.project_str)
        (self.project / "memory" / "seed.md").write_text("seed commit\n")
        _git_commit_all(self.project_str, "seed")
        # write the approved text to an unwatched file but DO NOT commit it
        (self.project / "memory" / "leak.md").write_text(APPROVED + "\n")
        path = str(self.project / "drafts" / "decoy.md")
        rc, _, _ = run_hook(
            write_payload(path, APPROVED),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)

    def test_fresh_drafting_blocks(self):
        """Content with no prior appearance anywhere -> BLOCK (TRUE POSITIVE)."""
        path = str(self.project / "drafts" / "fresh.md")
        rc, _, stderr = run_hook(
            write_payload(path, FRESH),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)
        self.assertIn("No persist-provenance", stderr)

    def test_whitespace_reformatted_match_passes(self):
        """Reflowed content (whitespace changed) still fingerprint-matches."""
        tp = _write_transcript(self.project_str, [
            ("user", "approved, here it is:\n" + APPROVED),
            ("assistant", "writing now"),
        ])
        reflowed = APPROVED.replace("\n", "\n\n   ").replace(" ", "  ")
        path = str(self.project / "drafts" / "reflowed.md")
        payload = write_payload(path, reflowed)
        payload["transcript_path"] = tp
        rc, _, _ = run_hook(payload, project_dir=self.project_str)
        self.assertEqual(rc, 0)


# ===========================================================================
# v0.2 — CANDIDATE C: mid-session single-use bypass
# ===========================================================================

class MidSessionBypassTests(HookHarness):

    def _make_bypass(self, session_id: str, nonce: str, reason: str,
                     age_s: float = 0.0) -> str:
        path = f"{RA.BYPASS_FILE_PREFIX}{session_id}-{nonce}"
        Path(path).write_text(reason)
        if age_s:
            old = time.time() - age_s
            os.utime(path, (old, old))
        self._tmp_bypass_files.append(path)
        return path

    def test_midsession_bypass_file_passes(self):
        sid = "ms-pass-1"
        bf = self._make_bypass(sid, "n1", "hub-protected synthesis triage")
        path = str(self.project / "scripts" / "big.py")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 1000, session_id=sid),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(bf), "bypass file must be consumed")
        entry = json.loads(self.bypass_log.read_text().splitlines()[-1])
        self.assertEqual(entry["mode"], "bypass-midsession")
        self.assertEqual(entry["reason"], "hub-protected synthesis triage")

    def test_midsession_bypass_consumed_not_reusable(self):
        sid = "ms-once-1"
        self._make_bypass(sid, "n1", "single use")
        path = str(self.project / "scripts" / "big.py")
        rc1, _, _ = run_hook(
            write_payload(path, "x" * 1000, session_id=sid),
            project_dir=self.project_str,
        )
        rc2, _, _ = run_hook(
            write_payload(path, "x" * 1000, session_id=sid),
            project_dir=self.project_str,
        )
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 2)  # file already consumed

    def test_midsession_bypass_stale_blocks(self):
        sid = "ms-stale-1"
        self._make_bypass(sid, "n1", "too old", age_s=700)  # > 600s TTL
        path = str(self.project / "scripts" / "big.py")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 1000, session_id=sid),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)

    def test_midsession_bypass_wrong_session_blocks(self):
        self._make_bypass("ms-other-sess", "n1", "not mine")
        path = str(self.project / "scripts" / "big.py")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 1000, session_id="ms-mine-sess"),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)


# ===========================================================================
# v0.2 — FAILURE SEMANTICS (fail-open vs fail-closed, distinct)
# ===========================================================================

class FailureSemanticsTests(HookHarness):

    def test_unreadable_transcript_falls_back_to_block(self):
        """transcript_path missing/corrupt -> fail closed re: pass -> BLOCK."""
        path = str(self.project / "drafts" / "x.md")
        payload = write_payload(path, FRESH)
        payload["transcript_path"] = str(self.project / "does-not-exist.jsonl")
        rc, _, _ = run_hook(payload, project_dir=self.project_str)
        self.assertEqual(rc, 2)

    def test_git_absent_falls_back_to_block(self):
        """No git repo -> git provenance unavailable -> fail closed -> BLOCK."""
        path = str(self.project / "drafts" / "x.md")
        rc, _, _ = run_hook(
            write_payload(path, APPROVED),
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)

    def test_latency_overrun_falls_back_to_block(self):
        """Budget exhausted -> git provenance aborted -> fail closed -> BLOCK.

        Same content + repo as test_persist_from_committed_blob_passes, which
        PASSES without the budget override — proves the abort, not a no-match.
        """
        _init_git_repo(self.project_str)
        (self.project / "memory" / "handoff.md").write_text(APPROVED + "\n")
        _git_commit_all(self.project_str, "approved")
        path = str(self.project / "drafts" / "x.md")
        rc, _, _ = run_hook(
            write_payload(path, APPROVED),
            env_overrides={"_ROUTING_ADVISOR_BUDGET_S": "0"},
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 2)

    def test_hook_crash_fails_open(self):
        """Top-level crash -> FAIL-OPEN -> exit 0 (distinct from fail-closed)."""
        path = str(self.project / "scripts" / "big.py")
        rc, _, _ = run_hook(
            write_payload(path, "x" * 1000),
            env_overrides={"_ROUTING_ADVISOR_TEST_CRASH": "1"},
            project_dir=self.project_str,
        )
        self.assertEqual(rc, 0)


# ===========================================================================
# v0.2 — FINGERPRINT INTERNALS (direct unit tests, spec §4.1)
# ===========================================================================

class FingerprintUnitTests(unittest.TestCase):

    def test_normalize_collapses_whitespace(self):
        self.assertEqual(RA.normalize("A  B\n\n\tC  "), "a b c")

    def test_content_matches_exact_substring(self):
        source = "lots of preamble text here " + APPROVED + " and a trailing tail"
        self.assertTrue(RA.content_matches(APPROVED, source))

    def test_content_matches_short_candidate_false(self):
        """Below the 400-char fingerprint floor -> never matches."""
        self.assertFalse(RA.content_matches("a short approved line", APPROVED))

    def test_content_matches_unrelated_false(self):
        unrelated = "completely different running prose about other topics " * 20
        self.assertFalse(RA.content_matches(APPROVED, unrelated))

    def test_has_contiguous_run_rejects_fragmented(self):
        """No shared 200-char run -> guard rejects (boilerplate protection)."""
        a = "a" * 150 + "b" * 150
        b = "a" * 150 + "c" * 150
        self.assertFalse(RA.has_contiguous_run(a, b))

    def test_has_contiguous_run_accepts_long_shared_run(self):
        a = "z" * 300
        b = "prefix " + "z" * 300 + " suffix"
        self.assertTrue(RA.has_contiguous_run(a, b))

    def test_user_text_skips_tool_result_blocks(self):
        """_user_text harvests text blocks but not tool_result blocks."""
        entry = {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "real operator words"},
            {"type": "tool_result", "tool_use_id": "t", "content": "TOOL OUTPUT"},
        ]}}
        out = RA._user_text(entry)
        self.assertIn("real operator words", out)
        self.assertNotIn("TOOL OUTPUT", out)

    def test_user_text_ignores_assistant_entry(self):
        entry = {"type": "assistant", "message": {"role": "assistant",
                 "content": [{"type": "text", "text": "hub authored"}]}}
        self.assertEqual(RA._user_text(entry), "")


if __name__ == "__main__":
    unittest.main()
