#!/usr/bin/env python3
"""PreToolUse hook for Agent: enforces Protocol 3 — ctx_batch_execute over Agent+Read.

When Echo launches an Agent whose prompt suggests multi-file reading (evals, audits,
scans, diagnostics), this hook WARNS that ctx_batch_execute should be used instead.

This is advisory (prints warning, does not block) because some Agent launches are
legitimate even for reads (e.g., when code changes are needed). But the warning
forces Echo to justify the choice.

Blocks ONLY when the Agent prompt contains 5+ file references AND read-heavy
keywords, making it near-certain ctx_batch_execute is the right tool.
"""

import json
import sys
import re


# Keywords that signal "this agent will mostly read files"
READ_SIGNALS = [
    "read-only", "read only", "do not edit",
    "evaluate", "eval ", "audit", "scan", "review",
    "check each", "check all", "read each", "read all",
    "score against", "grade against",
]

# Phrases that NEGATE write intent (must check before WRITE_SIGNALS)
WRITE_NEGATIONS = [
    "do not edit", "don't edit", "no edit", "not edit",
    "do not write", "don't write", "no write",
    "do not modify", "don't modify", "no modif",
    "do not create", "don't create",
    "read-only", "read only", "research only",
]

# Keywords that signal "this agent needs to write/change things" (legitimate Agent use)
WRITE_SIGNALS = [
    "fix", "edit", "write", "create", "update", "modify",
    "build", "implement", "refactor", "deploy",
]

# Pattern for file paths in prompt (must look like actual paths, not bare extensions)
FILE_PATH_PATTERN = re.compile(
    r'(?:\.claude/|memory/|echo/|docs/|drafts/|SKILL\.md|\w+\.py|\w+\.json|\w+\.md)',
    re.IGNORECASE,
)

WARNING = """⚠️  PROTOCOL 3 — TOOL ROUTING CHECK
This Agent prompt looks like a multi-file READ task.
ctx_batch_execute can do this in ONE call — no subagent needed.
Saves tokens, avoids usage limits, keeps data indexed for ctx_search.

If this Agent MUST write/change files, proceed. Otherwise, cancel and use:
  mcp__plugin_context-mode_context-mode__ctx_batch_execute

Evidence: {evidence}"""

BLOCK_MSG = """PROTOCOL 3 BLOCK: This Agent prompt references {file_count}+ files for read-only work.
Use ctx_batch_execute instead. It runs in-process (no usage limits), auto-indexes
results (searchable via ctx_search), and saves ~40% tokens.

Rewrite this as a ctx_batch_execute call with commands + queries."""


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name != "Agent":
        sys.exit(0)

    tool_input = data.get("tool_input", {})
    prompt = tool_input.get("prompt", "").lower()

    if not prompt:
        sys.exit(0)

    # Count read signals
    read_hits = [s for s in READ_SIGNALS if s in prompt]

    # Check for write negations first ("do not edit" is NOT a write signal)
    has_write_negation = any(n in prompt for n in WRITE_NEGATIONS)

    # Count write signals (these make Agent legitimate)
    # But if negated ("do not edit"), don't count them
    if has_write_negation:
        write_hits = []
    else:
        write_hits = [s for s in WRITE_SIGNALS if s in prompt]

    # Count file path references
    file_refs = FILE_PATH_PATTERN.findall(prompt)
    file_count = len(set(file_refs))

    # Decision logic
    # If write signals present, Agent is probably legitimate
    if write_hits:
        sys.exit(0)

    # BLOCK: 5+ file refs AND read-only signals AND no write signals
    if file_count >= 5 and read_hits and not write_hits:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": BLOCK_MSG.format(file_count=file_count),
            }
        }
        print(json.dumps(output))
        sys.exit(0)

    # WARN: 2+ read signals OR 3+ file refs (but not enough to block)
    if len(read_hits) >= 2 or file_count >= 3:
        evidence = f"{len(read_hits)} read signals ({', '.join(read_hits[:3])}), {file_count} file refs"
        # Print warning to stderr (advisory, not blocking)
        print(WARNING.format(evidence=evidence), file=sys.stderr)
        sys.exit(0)

    # No signals — let it through
    sys.exit(0)


if __name__ == "__main__":
    main()
