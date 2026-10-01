#!/usr/bin/env python3
# Claude Code PreToolUse Hook: Storyboard Entry Sentinel
# Enforces LAW 19 (clone-winning-competitor-patterns-first) on new storyboard creation.

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    # Fail-open if PyYAML is not installed
    sys.exit(0)

BLOCK_MESSAGE_TEMPLATE = """\
[STORYBOARD-ENTRY-SENTINEL] Write refused: {rel_path}

New storyboard creation requires LAW 19 clone-source discipline.

Missing or invalid frontmatter fields:
{error_list}

Required frontmatter (YAML between --- markers at file top):
  clone_source: PBL §X.Y / @competitor [proof metric]   # or NOVEL with synthesis_justification
  reference_assets:
    - <path to PBL §22 corpus file>
    - <or echo/scrape/ path>
    - <or competitor URL>
  synthesis_justification: <reason>   # only required if clone_source contains NOVEL

Bypass for legitimate exception cases:
  STORYBOARD_NOVEL_REASON="<short reason>" Write <path>
or
  SENTINEL_OVERRIDE=storyboard-entry-sentinel Write <path>
All bypass usage logged to .claude/hooks/storyboard-entry-log.jsonl for review.

See: docs/superpowers/specs/2026-05-24-S373-storyboard-entry-sentinel.md
"""


def get_repo_root() -> Path:
    """Determine the repository root from env var or script location."""
    if project_dir := os.environ.get("CLAUDE_PROJECT_DIR"):
        return Path(project_dir)
    # Fallback: .claude/hooks/storyboard-entry-sentinel.py -> ../../..
    return Path(__file__).resolve().parent.parent.parent


def log_action(repo_root: Path, file_path: str, action: str, details: dict | None = None):
    """Append a JSONL entry to the hook's log file."""
    log_file = repo_root / ".claude/hooks/storyboard-entry-log.jsonl"
    session = os.environ.get("ECHO_SESSION", "unknown")
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")

    log_entry = {
        "ts": timestamp,
        "session": session,
        "file": file_path,
        "action": action,
    }
    if details:
        log_entry.update(details)

    try:
        log_file.parent.mkdir(exist_ok=True)
        with log_file.open("a") as f:
            f.write(json.dumps(log_entry) + "\n")
    except OSError:
        # Fail-open: never let logging failures break the hook.
        pass


def parse_frontmatter(content: str) -> tuple[dict | None, str | None]:
    """Extract and parse YAML frontmatter from file content."""
    if not content.startswith("---"):
        return None, "no_block"

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, "no_block"

    frontmatter_str = parts[1]
    try:
        data = yaml.safe_load(frontmatter_str)
        if not isinstance(data, dict):
            return None, "parse_error"
        return data, None
    except yaml.YAMLError:
        return None, "parse_error"


def validate_frontmatter(frontmatter: dict) -> list[tuple[str, str]]:
    """Apply validation rules to the parsed frontmatter."""
    errors = []
    clone_source = frontmatter.get("clone_source")
    if not isinstance(clone_source, str) or not clone_source.strip():
        errors.append(("clone_source", "Field is required and must be a non-empty string."))

    ref_assets = frontmatter.get("reference_assets")
    if not ref_assets:
        errors.append(("reference_assets", "Field is required and cannot be empty (must be a non-empty string or list)."))

    if clone_source and isinstance(clone_source, str) and "novel" in clone_source.lower():
        synth_just = frontmatter.get("synthesis_justification")
        if not isinstance(synth_just, str) or not synth_just.strip():
            errors.append(("synthesis_justification", "Field is required when clone_source contains 'NOVEL'."))
    
    return errors


def run():
    """Main hook logic."""
    repo_root = get_repo_root()
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # Fail-open on malformed payload

    tool_name = payload.get("tool_name", payload.get("tool"))
    if tool_name != "Write":
        return 0

    tool_input = payload.get("tool_input", payload.get("arguments", {}))
    file_path_str = tool_input.get("file_path")
    if not file_path_str:
        return 0

    try:
        # Resolve path relative to repo root
        abs_path = Path(file_path_str)
        if not abs_path.is_absolute():
            abs_path = (repo_root / file_path_str).resolve()
        rel_path = abs_path.relative_to(repo_root)
    except (ValueError, OSError):
        return 0 # Fail-open if path is weird

    rel_path_posix = rel_path.as_posix()

    # Check for bypasses first
    if reason := os.environ.get("STORYBOARD_NOVEL_REASON"):
        log_action(repo_root, rel_path_posix, "allowed", {"bypass_type": "reason", "reason": reason})
        return 0
    if os.environ.get("SENTINEL_OVERRIDE") == "storyboard-entry-sentinel":
        log_action(repo_root, rel_path_posix, "allowed", {"bypass_type": "sentinel_override"})
        return 0

    # Scope checks
    path_parts = rel_path.parts
    is_in_scope = len(path_parts) > 2 and path_parts[0] == 'drafts' and path_parts[1] in ('storyboard', 'scheduled')
    is_archived = len(path_parts) > 2 and path_parts[0] == 'drafts' and path_parts[1] == 'archive'

    if not is_in_scope or is_archived:
        return 0
    
    # Edit-via-Write case (legacy file): allow edits without enforcement
    if abs_path.exists():
        return 0

    content = tool_input.get("content", "")
    frontmatter, parse_status = parse_frontmatter(content)

    if parse_status == "parse_error":
        return 0 # Fail-open on YAML parsing errors
    
    if parse_status == "no_block":
        errors = [("frontmatter", "No valid YAML frontmatter block (---) found at file start.")]
        error_list_str = "\n".join(f"  - {field}: {reason}" for field, reason in errors)
        sys.stderr.write(BLOCK_MESSAGE_TEMPLATE.format(rel_path=rel_path_posix, error_list=error_list_str))
        log_action(repo_root, rel_path_posix, "blocked", {"missing_fields": ["frontmatter_block"]})
        return 2

    validation_errors = validate_frontmatter(frontmatter)

    if validation_errors:
        error_list_str = "\n".join(f"  - {field}: {reason}" for field, reason in validation_errors)
        sys.stderr.write(BLOCK_MESSAGE_TEMPLATE.format(rel_path=rel_path_posix, error_list=error_list_str))
        log_action(repo_root, rel_path_posix, "blocked", {"missing_fields": [field for field, _ in validation_errors]})
        return 2

    log_action(repo_root, rel_path_posix, "allowed")
    return 0


if __name__ == "__main__":
    # Fail-open: any unexpected exception should not block the user.
    try:
        sys.exit(run())
    except Exception:
        sys.exit(0)
