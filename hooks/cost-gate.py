#!/usr/bin/env python3
"""PreToolUse hook for Bash: flags paid API calls that may need cost approval.

Advisory only — prints a reminder, does not block.
Enforces rules: "No auto-execute without consent" + "Cost transparency"
"""

import json
import sys

# Paid API domains/patterns and their service names
PAID_APIS = {
    "api.elevenlabs.io": "ElevenLabs (voice generation)",
    "elevenlabs.io/v1": "ElevenLabs (voice generation)",
    "aiquickdraw.com": "kie.ai (image/video generation)",
    "kie.ai": "kie.ai (image/video generation)",
    "api.vidu.com": "Vidu (video generation)",
    "api.openai.com": "OpenAI (API call)",
    "api.midjourney": "Midjourney (image generation)",
    "api.runwayml": "Runway (video generation)",
}

# Render scripts that are free but resource-heavy (thermal limit on 2015 MacBook)
RENDER_SCRIPTS = {
    "reel_screenrec.py": "Reel screen recorder (CPU-heavy)",
    "carousel.py": "Carousel renderer (CPU-heavy)",
}

COST_REMINDER = (
    "💰 COST GATE: Paid API call detected → {service}. "
    "Did you present a cost estimate to the operator before this call? "
    "If not, present the estimated cost and get approval before proceeding."
)

RENDER_REMINDER = (
    "🖥️ RENDER GATE: Resource-heavy script detected → {service}. "
    "Batch limit: max 3 reels per batch (thermal limit). "
    "Confirm batch size is within limits."
)


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    command_lower = command.lower()

    # Check for paid API calls
    for domain, service in PAID_APIS.items():
        if domain.lower() in command_lower:
            print(COST_REMINDER.format(service=service))
            sys.exit(0)

    # Check for render scripts
    for script, service in RENDER_SCRIPTS.items():
        if script.lower() in command_lower:
            print(RENDER_REMINDER.format(service=service))
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
