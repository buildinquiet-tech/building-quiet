#!/usr/bin/env python3
"""PBL Pre-Read Sentinel — PreToolUse hook.

Blocks content artifact creation (storyboard files, render commands, scheduled
drafts) until the relevant control-point references have been read in the
current session.

Generalizes feedback_openart_pre_read_required.md beyond OpenArt to all
content production. Pattern: pre-read before first artifact tool call.

Triggers (block-eligible):
  - Write/Edit to drafts/storyboard/, drafts/scheduled/, drafts/brand-qa/
  - Bash commands invoking video render (ffmpeg with output .mp4, kie.ai
    spoke calls for video/avatar, kling, veo, seedance, sora, hailuo, wan,
    suno music compose)
  - MCP generative video/voice/music actions (kie-ai__kling_avatar,
    kie-ai__veo3_*, kie-ai__suno_*, elevenlabs__text_to_speech)

Required pre-reads (must appear in current-day transcript):
  - memory/reference_pbl.md (universal — viral content frameworks)
  - .claude/rules/content-voice.md (universal style rules)
  - Brand-specific:
    - @yourbrand1 → .claude/rules/content-voice-@yourbrand1.md
    - @yourbrand2 (BIQ) → .claude/rules/content-voice-yourbrand2.md
    - @yourbrand3 → .claude/rules/content-voice-@yourbrand3.md

Override: SENTINEL_OVERRIDE=pbl bypasses for one tool call.
"""

import json
import sys
import os
import re
import glob
from datetime import datetime

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HOOKS_DIR)
REPO_DIR = os.path.dirname(PROJECT_DIR)
TRANSCRIPT_DIR = os.path.join(REPO_DIR, "echo", "transcripts")

# Claude Code's live JSONL session transcripts capture ALL tool uses (incl. Read);
# echo/transcripts only captures Edit|Write|Bash via transcript-enforcer.
_project_hash = REPO_DIR.replace("/", "-")
JSONL_TRANSCRIPT_DIR = os.path.expanduser(
    f"~/.claude/projects/{_project_hash}"
)
JSONL_RECENCY_HOURS = 24

CONTENT_PATH_PATTERNS = (
    "/drafts/storyboard/",
    "/drafts/scheduled/",
    "/drafts/brand-qa/",
    "/drafts/published/",
)

RENDER_BASH_PATTERNS = (
    r"\bffmpeg\b.*\.mp4\b",
    r"\bffmpeg\b.*\.mov\b",
)

RENDER_MCP_SUFFIXES = (
    "kling_avatar", "kling_video", "veo3_generate_video", "veo3_get_1080p_video",
    "sora_video", "hailuo_video", "wan_video", "wan_animate",
    "bytedance_seedance_video", "runway_aleph_video",
    "infinitalk_lip_sync",
    "suno_generate_music", "compose_music",
    "text_to_speech", "text_to_voice",
)

REQUIRED_UNIVERSAL = [
    ("PBL Bible", "reference_pbl.md"),
    ("Universal voice rules", "content-voice.md"),
]

BRAND_PRE_READS = {
    "@yourbrand1":    ("@yourbrand1 voice", "content-voice-@yourbrand1.md"),
    "yourbrand2": ("@yourbrand2 voice", "content-voice-yourbrand2.md"),
    "biq":       ("@yourbrand2 voice", "content-voice-yourbrand2.md"),
    "@yourbrand3": ("@yourbrand3 voice", "content-voice-@yourbrand3.md"),
}

BRAND_DETECT_PATTERNS = {
    "@yourbrand1":       re.compile(r"@?@yourbrand1|content-voice-@yourbrand1", re.IGNORECASE),
    "yourbrand2": re.compile(r"@?build\.?inquiet|BIQ|BQ-\d|content-voice-yourbrand2", re.IGNORECASE),
    "@yourbrand3": re.compile(r"@?@yourbrand3|content-voice-@yourbrand3", re.IGNORECASE),
}


def is_content_artifact(tool_name, tool_input):
    """Returns (is_artifact: bool, artifact_text: str) for brand detection."""
    if not isinstance(tool_input, dict):
        return False, ""

    if tool_name in ("Write", "Edit"):
        path = (tool_input.get("file_path") or "").lower()
        if any(p in path for p in CONTENT_PATH_PATTERNS):
            text = (tool_input.get("content") or "") + " " + path
            return True, text
        return False, ""

    if tool_name == "Bash":
        cmd = tool_input.get("command") or ""
        for pat in RENDER_BASH_PATTERNS:
            if re.search(pat, cmd, re.IGNORECASE):
                return True, cmd
        return False, ""

    if tool_name.startswith("mcp__"):
        suffix = tool_name.split("__")[-1]
        if suffix in RENDER_MCP_SUFFIXES:
            text = json.dumps(tool_input)
            return True, text

    return False, ""


def detect_brand(text):
    """Returns brand key or None."""
    for brand, pat in BRAND_DETECT_PATTERNS.items():
        if pat.search(text):
            return brand
    return None


def get_current_transcript():
    """Returns path to today's transcript or most recent if today missing."""
    if not os.path.isdir(TRANSCRIPT_DIR):
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    today_path = os.path.join(TRANSCRIPT_DIR, f"{today}.md")
    if os.path.exists(today_path):
        return today_path
    files = sorted(glob.glob(os.path.join(TRANSCRIPT_DIR, "*.md")), reverse=True)
    return files[0] if files else None


def transcript_mentions(needle):
    """Check if today's transcript OR any recent JSONL session mentions a filename.

    Two sources:
      1. echo/transcripts/{today}.md — markdown transcript (Edit|Write|Bash only)
      2. ~/.claude/projects/.../{session}.jsonl — Claude Code's live transcript
         (ALL tool uses, including Read). Scans files modified within
         JSONL_RECENCY_HOURS to capture the active session.
    """
    needle_lower = needle.lower()

    # Source 1: Echo markdown transcript
    path = get_current_transcript()
    if path:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                if needle_lower in f.read().lower():
                    return True
        except (OSError, UnicodeDecodeError):
            pass

    # Source 2: Claude Code JSONL session transcripts (recent)
    if os.path.isdir(JSONL_TRANSCRIPT_DIR):
        cutoff = datetime.now().timestamp() - (JSONL_RECENCY_HOURS * 3600)
        try:
            for fname in os.listdir(JSONL_TRANSCRIPT_DIR):
                if not fname.endswith(".jsonl"):
                    continue
                fpath = os.path.join(JSONL_TRANSCRIPT_DIR, fname)
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        continue
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        if needle_lower in f.read().lower():
                            return True
                except (OSError, UnicodeDecodeError):
                    continue
        except OSError:
            pass

    return False


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "pbl":
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    is_art, text = is_content_artifact(tool_name, tool_input)
    if not is_art:
        sys.exit(0)

    brand = detect_brand(text)

    missing = []
    for label, fname in REQUIRED_UNIVERSAL:
        if not transcript_mentions(fname):
            missing.append(f"  - {label} ({fname})")

    if brand and brand in BRAND_PRE_READS:
        label, fname = BRAND_PRE_READS[brand]
        if not transcript_mentions(fname):
            missing.append(f"  - {label} ({fname})")

    if not missing:
        sys.exit(0)

    brand_str = f" for brand={brand}" if brand else ""
    msg = (
        f"[PBL PRE-READ HALT] About to create content artifact{brand_str} "
        f"without reading control points this session.\n"
        f"Missing pre-reads:\n" + "\n".join(missing) + "\n\n"
        f"Required: Read each file via the Read tool BEFORE producing content. "
        f"Then re-attempt this tool call.\n"
        f"Override (one call): SENTINEL_OVERRIDE=pbl"
    )

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
