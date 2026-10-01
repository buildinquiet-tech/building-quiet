#!/usr/bin/env python3
"""metricool-post-verify-pre.py — PreToolUse Bash hook.

Blocks Metricool POSTs to /scheduler/posts whose payload describes a
media-required platform schedule (IG Reel/Carousel, YT Short, TikTok video)
with an empty or missing `media` array. This is the exact failure mode
that destroyed the S287 batch (18 broken schedules) and two later TikTok posts (S307).

Threads and text-only providers are NEVER blocked.

Exit 0 = allow. Exit 2 + stderr = block (Claude Code halts the tool call).
"""

import json
import re
import sys


BLOCK_MESSAGE = """BLOCKED — Metricool POST has empty media for a media-required platform.

Detected payload signals: {signals}

This is the S287 failure mode. Metricool will accept the POST,
return 201, and silently fail at autopublish time with provider error
"You need to add a picture to make a Instagram post" (or equivalent).

To proceed:
  1. Upload your media to Litterbox (72h expiry) or another HTTPS host
     curl -F "reqtype=fileupload" -F "time=72h" -F "fileToUpload=@FILE" \\
       https://litterbox.catbox.moe/resources/internals/api.php
  2. Add the URL to the `media` array in the payload:
     "media": ["https://litterbox.catbox.moe/<token>.mp4"]
  3. Re-fire the POST.

If you genuinely intended a text-only post on a text-capable platform
(Threads), make sure the payload's only `providers[].network` is one of
{{"threads"}} — this hook only blocks media-required platforms.

See: docs/_active/publishing-rules.md > "Per-content-type Metricool payload shape"
See: memory/feedback_metricool_media_attach_verify.md
"""


def looks_like_post(command_lower: str) -> bool:
    if "metricool.com" not in command_lower:
        return False
    if "/scheduler/posts" not in command_lower:
        return False
    if "-x get" in command_lower or '"get"' in command_lower:
        return False
    if "-x delete" in command_lower or "method='delete'" in command_lower:
        return False
    post_signals = ("-x post", "method='post'", 'method="post"', "method=post",
                    "post_new", "scheduler/posts?", "post(", " -d ", " --data")
    return any(sig in command_lower for sig in post_signals)


def extract_payload_text(command: str) -> str:
    for flag in (r"-d\s+'([^']+)'", r'-d\s+"([^"]+)"',
                 r"--data\s+'([^']+)'", r'--data\s+"([^"]+)"'):
        m = re.search(flag, command)
        if m:
            return m.group(1)
    # Fallback: unescape backslash-escaped quotes so embedded JSON is readable
    unescaped = command.replace('\\"', '"').replace("\\'", "'")
    return unescaped


def is_media_required_payload(payload_text: str):
    """Return (is_media_required, list of detected platform signals)."""
    lower = payload_text.lower()
    signals = []

    # IG Reel
    if "instagramdata" in lower and re.search(r"['\"]?type['\"]?\s*:\s*['\"]reel['\"]", lower):
        signals.append("IG Reel")
    # IG Carousel / POST type — also media-required
    if "instagramdata" in lower and re.search(r"['\"]?type['\"]?\s*:\s*['\"]post['\"]", lower):
        signals.append("IG Carousel/Post")
    # YT Short / video
    if "youtubedata" in lower and re.search(r"['\"]?type['\"]?\s*:\s*['\"](short|video)['\"]", lower):
        signals.append("YT Short/Video")
    # TikTok — always video
    if re.search(r"['\"]?network['\"]?\s*:\s*['\"]tiktok['\"]", lower):
        signals.append("TikTok")

    return bool(signals), signals


def has_empty_or_missing_media(payload_text: str) -> bool:
    # missing field entirely (no `media:` key on a media-required payload)
    if not re.search(r"['\"]?media['\"]?\s*:", payload_text):
        return True
    # explicit null
    if re.search(r"['\"]?media['\"]?\s*:\s*null", payload_text):
        return True
    # empty string
    if re.search(r"['\"]?media['\"]?\s*:\s*['\"]['\"]", payload_text):
        return True
    # empty array
    if re.search(r"['\"]?media['\"]?\s*:\s*\[\s*\]", payload_text):
        return True
    # array with only empty strings
    if re.search(r"['\"]?media['\"]?\s*:\s*\[\s*['\"][\s]*['\"](?:\s*,\s*['\"][\s]*['\"])*\s*\]", payload_text):
        return True
    return False


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(0)

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    if not looks_like_post(command.lower()):
        sys.exit(0)

    payload = extract_payload_text(command)
    is_required, signals = is_media_required_payload(payload)
    if not is_required:
        sys.exit(0)

    if has_empty_or_missing_media(payload):
        print(BLOCK_MESSAGE.format(signals=signals or ["unknown media-required"]),
              file=sys.stderr)
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
