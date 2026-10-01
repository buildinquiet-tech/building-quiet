#!/usr/bin/env python3
"""PostToolUse hook: detects media files written inside the repo instead of Echo OUT.

Advisory only — prints a reminder, does not block.
Enforces rule: "Stage renders to ~/Documents/Echo-Exports/ immediately."
"""

import json
import os
import sys

# Media extensions that should go to Echo OUT, not the repo
MEDIA_EXTENSIONS = {
    ".mp4", ".mp3", ".wav", ".m4a",  # audio/video
    ".png", ".jpg", ".jpeg", ".gif", ".webp",  # images
    ".pdf",  # documents
}

# Repo root (normalized)
REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Paths inside the repo that are EXEMPT (media is expected here)
EXEMPT_PATHS = {
    os.path.join(REPO_ROOT, "echo", "mac-reports"),  # Mac App PNGs
}

REMINDER = (
    "⚠️ ECHO OUT RULE: Media file detected inside repo. "
    "Stage to ~/Documents/Echo-Exports/ immediately. "
    "Never give the operator repo paths — only Echo-Exports paths."
)


def has_media_extension(path):
    """Check if a path ends with a media extension."""
    _, ext = os.path.splitext(path.lower())
    return ext in MEDIA_EXTENSIONS


def is_exempt(path):
    """Check if a path is in an exempt directory."""
    norm = os.path.normpath(path)
    for exempt in EXEMPT_PATHS:
        if norm.startswith(exempt):
            return True
    return False


def is_inside_repo(path):
    """Check if a path is inside the Echo repo."""
    norm = os.path.normpath(path)
    return norm.startswith(REPO_ROOT)


def check_bash_output(data):
    """Check Bash tool output for media files written inside repo."""
    output = data.get("tool_result", {})
    if isinstance(output, dict):
        output = output.get("stdout", "") or output.get("output", "")
    if not isinstance(output, str):
        return False

    # Also check the original command for Write-like patterns
    command = data.get("tool_input", {}).get("command", "")

    # Check output lines for repo paths with media extensions
    for line in (output + "\n" + command).split("\n"):
        line = line.strip()
        if not line:
            continue
        # Look for paths containing the repo root
        if REPO_ROOT in line:
            # Extract potential file paths from the line
            for token in line.split():
                token = token.strip("'\"")
                if is_inside_repo(token) and has_media_extension(token) and not is_exempt(token):
                    return True
    return False


def check_write_tool(data):
    """Check Write tool for media files written inside repo."""
    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return False
    return is_inside_repo(file_path) and has_media_extension(file_path) and not is_exempt(file_path)


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")

    triggered = False
    if tool_name == "Bash":
        triggered = check_bash_output(data)
    elif tool_name == "Write":
        triggered = check_write_tool(data)

    if triggered:
        print(REMINDER)

    sys.exit(0)


if __name__ == "__main__":
    main()
