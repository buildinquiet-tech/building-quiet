#!/usr/bin/env python3
"""PreToolUse hook — routing-advisor v0.2 (calibration: persist vs hub-drafting).

v0.1 blocked substantive Write/Edit on drafting-class paths by SIZE + PATH
only. That cannot tell "drafting new content on the hub" (block) from
"persisting already-drafted/approved content to disk" (legitimate). v0.2
adds a provenance layer that classifies by COGNITION LOCATION.

Spec: docs/superpowers/specs/2026-05-20-routing-advisor-calibration.md

Decision flow:
  Write/Edit on watched path, over size threshold?
    no  -> exit 0 (PASS)
    yes -> B: content fingerprint found in a genuine operator message
             OR in the git HEAD committed tree?
            yes -> exit 0 (PASS, log mode=persist-provenance:*)
            no  -> C: fresh single-use /tmp bypass file for this session?
                    yes -> consume file, exit 0 (PASS, log mode=bypass-midsession)
                    no  -> env-var bypass set?
                            yes -> exit 0 (PASS, log mode=bypass-env)
                            no  -> exit 2 (BLOCK — genuine hub-drafting)

Provenance is deliberately narrow (S368 live-test findings):
  - Transcript provenance counts ONLY genuine operator (user) text. Assistant
    text and assistant tool_use inputs never count — the hub authored those,
    so they cannot prove content "pre-existed" the hub's generation. This
    closes the retry-hole (a blocked Write's tool_use input lands in the
    transcript) and the /tmp-Read decoy (tool_result blocks are skipped).
  - git provenance counts ONLY committed HEAD blobs, never the working tree
    (closes the decoy-Write-to-unwatched-file hole).
  - Spoke-drafted content that is neither pasted by the operator nor yet
    committed passes via Candidate C (logged bypass) — Candidate A (a spoke
    pass-file) is the v0.3 mechanism for that class.

Failure semantics (two distinct layers, named precisely):
  - Top-level hook crash -> FAIL-OPEN (exit 0). A governance-hook bug must
    never brick Write/Edit.
  - Provenance-subsystem error / latency overrun -> FAILS CLOSED relative to
    pass eligibility: it never produces a pass, it falls through to the
    block path. Not the same posture as fail-open.

Bypass (3 paths, all logged to routing-bypass-log.jsonl, audited weekly):
  - persist-provenance — automatic, when content provenance is established
  - bypass-midsession  — /tmp/.routing-advisor-bypass-{session}-{nonce},
                         single-use (consumed on read), TTL-capped
  - bypass-env         — ECHO_ROUTING_REASON env var (launch-time)

Tunable env (documented):
  - _ROUTING_ADVISOR_BUDGET_S — override the 1.5s provenance latency budget
  - _ROUTING_ADVISOR_TEST_CRASH=1 — force a top-level crash (fail-open test)

Cross-refs:
  - Doctrine: memory/feedback_hub_trust_spokes_economic.md
  - Routing matrix: docs/_active/task-model-routing.md §11
  - Telemetry pair: .claude/hooks/bulk-burn-tracker.py
"""

from __future__ import annotations

import glob as _glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path, PurePath

PROJECT_DIR = Path(os.environ.get(
    "CLAUDE_PROJECT_DIR",
    Path(__file__).resolve().parent.parent.parent,
)).resolve()

GLOB_PATTERNS = ["scripts/*.py", "scripts/*.sh", ".claude/hooks/*.py"]
NESTED_PREFIX = "drafts"

WRITE_CONTENT_THRESHOLD = 500
EDIT_NEW_STRING_THRESHOLD = 300
EDIT_DELTA_THRESHOLD = 200

BYPASS_ENV_VAR = "ECHO_ROUTING_REASON"
BYPASS_LOG = PROJECT_DIR / ".claude" / "hooks" / "routing-bypass-log.jsonl"

# --- v0.2 provenance constants (spec §4.1; calibrated S368 against the
#     real repo — see PROVENANCE_BUDGET_S note) ---
FINGERPRINT_MIN_CHARS = 400      # below this, too short to fingerprint
SHINGLE_SIZE = 8                 # word-level shingle width
OVERLAP_THRESHOLD = 0.90         # min candidate-shingle overlap for a match
CONTIGUOUS_RUN_CHARS = 200       # boilerplate guard: required shared run
PROVENANCE_BUDGET_S = 1.50       # wall-clock budget (S368: git grep on the
                                 # real repo measured ~0.24-0.29s; 0.25 was
                                 # below a single git grep — calibrated up)
GIT_SUBPROC_TIMEOUT_S = 1.0      # per git invocation (S368 calibration)
# git grep is scoped to text extensions — persisted content is ~always
# markdown/code, never media; bounds the hot path (S368: ~0.16s vs ~0.24s).
GIT_TEXT_PATHSPEC = ["*.md", "*.py", "*.sh", "*.txt"]

BYPASS_FILE_TTL_S = 600          # 10 min — caps an unconsumed bypass file
BYPASS_FILE_PREFIX = "/tmp/.routing-advisor-bypass-"

_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------
# v0.1 path + size logic — unchanged
# --------------------------------------------------------------------------

def matches_watched(rel_path: str) -> bool:
    """Return True if rel_path is watched by this hook."""
    p = PurePath(rel_path)
    try:
        if p.is_relative_to(NESTED_PREFIX):
            return True
    except (ValueError, AttributeError):
        if rel_path == NESTED_PREFIX or rel_path.startswith(NESTED_PREFIX + "/"):
            return True
    return any(p.match(g) for g in GLOB_PATTERNS)


def relativize(file_path: str) -> str:
    """Return rel-to-PROJECT_DIR path. Falls back to raw input on escape/error."""
    if not file_path:
        return ""
    p = Path(file_path)
    if not p.is_absolute():
        p = PROJECT_DIR / p
    try:
        abs_path = p.resolve(strict=False)
        return str(abs_path.relative_to(PROJECT_DIR))
    except (ValueError, OSError):
        return file_path


def is_substantive(tool_name: str, tool_input: dict) -> tuple[bool, str, int, int]:
    """Return (substantive, trigger_label, threshold, observed)."""
    if tool_name == "Write":
        content = tool_input.get("content", "") or ""
        if not isinstance(content, str):
            return False, "write_invalid_content", 0, 0
        n = len(content)
        if n > WRITE_CONTENT_THRESHOLD:
            return True, "write_size_threshold", WRITE_CONTENT_THRESHOLD, n
        return False, "write_under_threshold", WRITE_CONTENT_THRESHOLD, n
    if tool_name == "Edit":
        new_string = tool_input.get("new_string", "") or ""
        old_string = tool_input.get("old_string", "") or ""
        if not isinstance(new_string, str) or not isinstance(old_string, str):
            return False, "edit_invalid_strings", 0, 0
        new_n = len(new_string)
        delta = new_n - len(old_string)
        if new_n > EDIT_NEW_STRING_THRESHOLD:
            return True, "edit_new_string_threshold", EDIT_NEW_STRING_THRESHOLD, new_n
        if delta > EDIT_DELTA_THRESHOLD:
            return True, "edit_delta_threshold", EDIT_DELTA_THRESHOLD, delta
        return False, "edit_under_threshold", EDIT_NEW_STRING_THRESHOLD, new_n
    return False, "tool_not_watched", 0, 0


def candidate_content(tool_name: str, tool_input: dict) -> str:
    """The text whose cognition-origin we want to classify."""
    if tool_name == "Write":
        c = tool_input.get("content", "")
    else:  # Edit
        c = tool_input.get("new_string", "")
    return c if isinstance(c, str) else ""


# --------------------------------------------------------------------------
# v0.2 fingerprinting (spec §4.1)
# --------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Lowercase; collapse all whitespace runs to one space; strip."""
    return _WS_RE.sub(" ", text.lower()).strip()


def shingles(norm_text: str, k: int = SHINGLE_SIZE) -> set:
    """Word-level k-shingles. Empty set if fewer than k words."""
    words = norm_text.split(" ")
    if len(words) < k:
        return set()
    return {tuple(words[i:i + k]) for i in range(len(words) - k + 1)}


def has_contiguous_run(norm_a: str, norm_b: str,
                       n: int = CONTIGUOUS_RUN_CHARS) -> bool:
    """True if any n-char window of norm_a appears verbatim in norm_b.

    Boilerplate guard: a high shingle overlap with no long contiguous run
    indicates shared template skeleton, not the same document.
    """
    if len(norm_a) <= n:
        return norm_a in norm_b
    for i in range(0, len(norm_a) - n + 1, 50):
        if norm_a[i:i + n] in norm_b:
            return True
    return norm_a[-n:] in norm_b


def content_matches(candidate: str, source: str) -> bool:
    """True if `candidate` is substantially present in `source`.

    Order (spec §4.1): exact normalized-substring first (cheap), then
    fuzzy shingle overlap gated by the contiguous-run boilerplate guard.
    """
    norm_c = normalize(candidate)
    if len(norm_c) < FINGERPRINT_MIN_CHARS:
        return False
    norm_s = normalize(source)
    if not norm_s:
        return False
    if norm_c in norm_s:
        return True
    cand_sh = shingles(norm_c)
    if not cand_sh:
        return False
    src_sh = shingles(norm_s)
    overlap = len(cand_sh & src_sh) / len(cand_sh)
    if overlap >= OVERLAP_THRESHOLD and has_contiguous_run(norm_c, norm_s):
        return True
    return False


# --------------------------------------------------------------------------
# v0.2 provenance source 1 — genuine operator messages in the transcript
#
# ONLY user-authored text counts. Assistant text and assistant tool_use
# inputs never count — the hub authored those, so they cannot establish that
# content "pre-existed" the hub's generation. Within a user message, only
# `text` content blocks are harvested; `tool_result` blocks (tool output,
# not operator input) are skipped. See module docstring for the two evasions
# this closes (retry-hole + /tmp-Read decoy).
# --------------------------------------------------------------------------

def _user_text(entry) -> str:
    """Extract genuine operator text from one transcript entry, else ""."""
    if not isinstance(entry, dict):
        return ""
    if entry.get("type") != "user":
        return ""
    msg = entry.get("message", {})
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            t = block.get("text", "")
            if isinstance(t, str):
                parts.append(t)
    return " ".join(parts)


def transcript_operator_text(transcript_path: str) -> str:
    """Concatenated text of every genuine operator (user) message.

    Any read/parse failure yields "" — fails closed (no provenance), never
    raises into a pass.
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.is_file():
        return ""
    try:
        raw = p.read_text(errors="replace")
    except OSError:
        return ""
    parts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        text = _user_text(entry)
        if text:
            parts.append(text)
    return "\n".join(parts)


# --------------------------------------------------------------------------
# v0.2 provenance source 2 — git HEAD committed tree (v0.2: HEAD only,
# no full-history git log -S — see spec §4.1 latency budget)
# --------------------------------------------------------------------------

def git_head_matches(candidate: str, deadline: float) -> bool:
    """True if `candidate` provenance is found in a file committed at HEAD.

    Working-tree-only (uncommitted) files are NOT searched — closes the
    decoy-Write hole (Echo writing fresh content to an unwatched file then
    'persisting' it). To pass via this source, content must be in a
    git-committed blob, an auditable act.
    """
    lines = [ln.strip() for ln in candidate.splitlines()]
    cand_lines = [ln for ln in lines if 40 <= len(ln) <= 300]
    if not cand_lines:
        return False
    pattern = max(cand_lines, key=len)
    try:
        res = subprocess.run(
            ["git", "-C", str(PROJECT_DIR), "grep", "-l", "-F",
             "-e", pattern, "HEAD", "--", *GIT_TEXT_PATHSPEC],
            capture_output=True, text=True, timeout=GIT_SUBPROC_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    if res.returncode != 0:
        return False
    for entry in res.stdout.splitlines():
        if time.monotonic() >= deadline:
            return False
        ref, sep, path = entry.partition(":")
        if not sep or not path:
            continue
        try:
            blob = subprocess.run(
                ["git", "-C", str(PROJECT_DIR), "show", f"{ref}:{path}"],
                capture_output=True, text=True, timeout=GIT_SUBPROC_TIMEOUT_S,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if blob.returncode == 0 and content_matches(candidate, blob.stdout):
            return True
    return False


def _budget_seconds() -> float:
    raw = os.environ.get("_ROUTING_ADVISOR_BUDGET_S", "")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return PROVENANCE_BUDGET_S


def check_provenance(candidate: str, transcript_path: str) -> str:
    """Return a non-empty mode string if persist-provenance is established,
    else "". Any error or latency overrun -> "" (fails closed re: pass).
    """
    deadline = time.monotonic() + _budget_seconds()
    try:
        operator_text = transcript_operator_text(transcript_path)
        if operator_text and content_matches(candidate, operator_text):
            return "persist-provenance:operator-message"
        if time.monotonic() >= deadline:
            return ""
        if git_head_matches(candidate, deadline):
            return "persist-provenance:git-head"
    except Exception:
        return ""
    return ""


# --------------------------------------------------------------------------
# v0.2 bypass C — mid-session single-use pass-file (consume-on-read)
# --------------------------------------------------------------------------

def consume_bypass_file(session_id: str):
    """Find a fresh single-use bypass file for this session, consume it
    (delete), and return its reason. Returns None if none valid.

    Single-use: the file is deleted on read, so one bypass authorizes
    exactly one Write/Edit — it cannot silently authorize a drafting burst.
    Stale files (older than TTL) are deleted and skipped.
    """
    if not session_id:
        return None
    now = time.time()
    pattern = f"{BYPASS_FILE_PREFIX}{session_id}-*"
    for path in sorted(_glob.glob(pattern)):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if now - st.st_mtime > BYPASS_FILE_TTL_S:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        try:
            reason = Path(path).read_text().strip()
        except OSError:
            reason = ""
        try:
            os.remove(path)  # consume-on-read
        except OSError:
            pass
        return reason or "(mid-session bypass — no reason given)"
    return None


# --------------------------------------------------------------------------
# logging + block message
# --------------------------------------------------------------------------

def append_bypass_log(entry: dict) -> None:
    """Append a pass record as JSONL (one record per line, append-safe)."""
    try:
        BYPASS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(BYPASS_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def block_message(rel_path: str, trigger: str, threshold: int,
                  observed: int) -> str:
    """Structured stderr: machine-readable header + human-readable doctrine."""
    return (
        f"[ROUTING-ADVISOR BLOCK]\n"
        f"classification: drafting-class\n"
        f"trigger: {trigger}\n"
        f"threshold: {threshold}\n"
        f"observed: {observed}\n"
        f"path: {rel_path}\n"
        f"---\n"
        f"No persist-provenance: this content has no prior appearance in a "
        f"genuine operator message or git HEAD — treated as fresh "
        f"hub-drafting.\n"
        f"Doctrine: feedback_hub_trust_spokes_economic.md — economic "
        f"work-types (drafting/synthesis/mechanical) route to spoke.\n"
        f"Options:\n"
        f"  1) Dispatch to spoke: python3 scripts/kie_chat.py --model sonnet-4-6\n"
        f"     (72% off direct; see docs/_active/task-model-routing.md §11)\n"
        f"  2) If this is a PERSIST of already-approved content, ensure the "
        f"approved text exists in an operator message or a git-committed "
        f"file — provenance then auto-passes.\n"
        f"  3) Mid-session single-use bypass: write a one-line reason to\n"
        f"     {BYPASS_FILE_PREFIX}{{session_id}}-{{nonce}}  (consumed on read).\n"
        f"  4) Launch-time bypass: export {BYPASS_ENV_VAR}=\"<reason>\".\n"
        f"  All bypasses are logged to .claude/hooks/routing-bypass-log.jsonl "
        f"and audited weekly."
    )


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # Documented test affordance — exercises the top-level fail-open contract.
    if os.environ.get("_ROUTING_ADVISOR_TEST_CRASH") == "1":
        raise RuntimeError("forced crash — fail-open contract test")

    tool_name = payload.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "")
    if not isinstance(file_path, str) or not file_path:
        sys.exit(0)

    rel_path = relativize(file_path)
    if not matches_watched(rel_path):
        sys.exit(0)

    substantive, trigger, threshold, observed = is_substantive(tool_name, tool_input)
    if not substantive:
        sys.exit(0)

    session_id = payload.get("session_id", "")
    candidate = candidate_content(tool_name, tool_input)

    base_log = {
        "ts": datetime.now().isoformat(),
        "session_id": session_id,
        "tool": tool_name,
        "file_path": rel_path,
        "trigger": trigger,
        "threshold": threshold,
        "observed": observed,
    }

    # --- B: persist-provenance (cognition-location classification) ---
    prov_mode = check_provenance(candidate, payload.get("transcript_path", ""))
    if prov_mode:
        append_bypass_log({**base_log, "mode": prov_mode,
                           "reason": "auto — content provenance established"})
        sys.exit(0)

    # --- C: mid-session single-use bypass file ---
    midsession_reason = consume_bypass_file(session_id)
    if midsession_reason is not None:
        append_bypass_log({**base_log, "mode": "bypass-midsession",
                           "reason": midsession_reason})
        sys.exit(0)

    # --- legacy: launch-time env-var bypass ---
    env_reason = os.environ.get(BYPASS_ENV_VAR, "").strip()
    if env_reason:
        append_bypass_log({**base_log, "mode": "bypass-env",
                           "reason": env_reason})
        sys.exit(0)

    # --- BLOCK: genuine hub-drafting, no provenance, no bypass ---
    sys.stderr.write(block_message(rel_path, trigger, threshold, observed) + "\n")
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Fail-open: governance hook crash must NOT brick Write/Edit.
        sys.exit(0)
