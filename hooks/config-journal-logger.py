#!/usr/bin/env python3
"""Config Journal Logger — PostToolUse hook.

Captures edits to config-tier paths (kernel, hooks, skills, settings,
decisions) into echo/config-journal.jsonl so approved changes don't
vanish into 24-hour fog.

Each journal entry: {ts, file, tool, session_surface, diff_preview,
current_approval_context}. Survives across Claude instances because
it's file-based, not session-based.

Advisory only. Never blocks. If logging fails, the tool call still
succeeds — journal integrity is a nice-to-have, not a safety barrier.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

# Config-tier paths worth journaling. Pattern-based so subdir
# extensions (new hook, new skill) work without code changes.
CONFIG_PATTERNS = [
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".mcp.json",
    ".claude/hooks/",
    ".claude/skills/",
    ".claude/rules/",
    "echo/board/decisions.md",
    "echo/board/action-items.md",
    "echo/board/meeting-log.md",
]

# Paths to SKIP inside skills/hooks dirs (avoid journal spam on
# routine artifact updates).
SKIP_PATTERNS = [
    ".claude/hooks/violation-log.json",
    ".claude/hooks/sentinel-override-log.json",
    ".claude/hooks/batch-counter-state.json",
    ".claude/skills/references/",
]


def is_config_path(path):
    """Return True if path matches a journaled config pattern."""
    if not path:
        return False
    for skip in SKIP_PATTERNS:
        if skip in path:
            return False
    for pattern in CONFIG_PATTERNS:
        if pattern in path:
            return True
    return False


def get_git_diff_preview(project_dir, path, max_lines=30):
    """Return first N lines of the uncommitted diff for this path.

    If path isn't tracked (gitignored or new), returns a short note.
    Never raises — logging is advisory.
    """
    try:
        # Relative path for git
        rel = os.path.relpath(path, project_dir) if path.startswith(project_dir) else path
        proc = subprocess.run(
            ["git", "-C", project_dir, "diff", "--no-color", "--", rel],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return f"(diff error: {proc.stderr.strip()[:120]})"
        diff = proc.stdout.strip()
        if not diff:
            # Maybe untracked — try status
            status_proc = subprocess.run(
                ["git", "-C", project_dir, "status", "--porcelain", "--", rel],
                capture_output=True, text=True, timeout=5,
            )
            if "??" in status_proc.stdout:
                return "(new file — not yet tracked)"
            return "(no diff — file may be gitignored)"
        lines = diff.split("\n")
        if len(lines) > max_lines:
            return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"
        return diff
    except Exception as e:
        return f"(diff unavailable: {type(e).__name__})"


def detect_surface():
    """Best-effort detection of which Claude surface is running.

    Env var CLAUDE_SURFACE if set; else parent process name hints.
    Returns: 'mac-app' | 'vscode' | 'cli' | 'unknown'.
    """
    surface = os.environ.get("CLAUDE_SURFACE", "").lower()
    if surface:
        return surface
    # Heuristic: check parent process
    try:
        ppid = os.getppid()
        proc = subprocess.run(
            ["ps", "-p", str(ppid), "-o", "comm="],
            capture_output=True, text=True, timeout=2,
        )
        name = proc.stdout.strip().lower()
        if "claude" in name and "mac" in name:
            return "mac-app"
        if "code" in name or "electron" in name:
            return "vscode"
        if "claude" in name:
            return "cli"
    except Exception:
        pass
    return "unknown"


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        sys.exit(0)

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "")
    if not is_config_path(file_path):
        sys.exit(0)

    project_dir = os.environ.get(
        "CLAUDE_PROJECT_DIR",
        os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..")),
    )
    journal_path = os.path.join(project_dir, "echo", "config-journal.jsonl")

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "file": os.path.relpath(file_path, project_dir) if file_path.startswith(project_dir) else file_path,
        "tool": tool_name,
        "surface": detect_surface(),
        "session_id": payload.get("session_id", "unknown")[:16],
        "diff_preview": get_git_diff_preview(project_dir, file_path),
    }

    try:
        os.makedirs(os.path.dirname(journal_path), exist_ok=True)
        with open(journal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
