#!/usr/bin/env python3
"""PostToolUse hook for Bash: reminds Echo of Protocol 4 when API calls are detected."""

import json
import sys


API_PATTERNS = ["curl", "wget"]
API_DOMAINS = ["metricool", "manychat", "litterbox", "stan.store", "elevenlabs", "kie.ai"]

REMINDER = """PROTOCOL 4 GATE FUNCTION ACTIVE: External API call detected.
Before claiming success:
1. IDENTIFY: What command proves this worked?
2. RUN: Execute verification
3. READ: Full output + status code
4. VERIFY: Does output confirm the claim?
Do NOT say "Done" until you show evidence."""


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    command = ""
    try:
        command = data.get("tool_input", {}).get("command", "")
    except AttributeError:
        sys.exit(0)

    if not command:
        sys.exit(0)

    command_lower = command.lower()

    for pattern in API_PATTERNS:
        if pattern in command_lower:
            print(REMINDER)
            sys.exit(0)

    for domain in API_DOMAINS:
        if domain in command_lower:
            print(REMINDER)
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
