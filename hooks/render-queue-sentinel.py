#!/usr/bin/env python3
"""Render Queue Sentinel — Stop hook.

Enforces pre-tag pattern for media renders (kie.ai / Higgsfield / OpenArt /
ElevenLabs). Renders are the highest dollar exposure in the system and the
most opaque drift class — call hangs, $ burned, no repo trace.

Trigger: Stop hook fires after assistant emits a stop signal.
Detection (any of):
  - MCP tool_use: mcp__kie-ai__* (kling_avatar, veo3, sora, etc. — write ops only)
  - MCP tool_use: mcp__higgsfield__generate_image / generate_video
  - MCP tool_use: mcp__elevenlabs__text_to_speech / voice_clone / etc.
  - Bash command POSTing to api.kie.ai render paths or higgsfield endpoints
    (kie.ai chat/LLM endpoints — /claude/, /openai/ — are EXCLUDED: they share
    the host with render endpoints but are not renders. S368 2026-05-20 fix.)
Required artifact: edit to `echo/render-queue.md` THIS session.

Override: SENTINEL_OVERRIDE=render-queue
"""

import json
import sys
import os
import re
import subprocess

RENDER_QUEUE_FILE = "render-queue.md"
ASSET_REGISTRY_BACKFILL = "scripts/asset-registry/asset-registry-backfill.py"

# MCP tool name prefixes that indicate render activity (write ops, not status checks)
RENDER_MCP_WRITE_TOOLS = {
    "mcp__kie-ai__kling_avatar",
    "mcp__kie-ai__veo3_generate_video",
    "mcp__kie-ai__sora_video",
    "mcp__kie-ai__hailuo_video",
    "mcp__kie-ai__bytedance_seedance_video",
    "mcp__kie-ai__bytedance_seedream_image",
    "mcp__kie-ai__flux2_image",
    "mcp__kie-ai__flux_kontext_image",
    "mcp__kie-ai__nano_banana_image",
    "mcp__kie-ai__openai_4o_image",
    "mcp__kie-ai__qwen_image",
    "mcp__kie-ai__midjourney_generate",
    "mcp__kie-ai__grok_imagine",
    "mcp__kie-ai__z_image",
    "mcp__kie-ai__ideogram_reframe",
    "mcp__kie-ai__topaz_upscale_image",
    "mcp__kie-ai__recraft_remove_background",
    "mcp__kie-ai__suno_generate_music",
    "mcp__kie-ai__elevenlabs_tts",
    "mcp__kie-ai__elevenlabs_ttsfx",
    "mcp__kie-ai__infinitalk_lip_sync",
    "mcp__kie-ai__wan_animate",
    "mcp__kie-ai__wan_video",
    "mcp__kie-ai__runway_aleph_video",
    "mcp__higgsfield__generate_image",
    "mcp__higgsfield__generate_video",
    "mcp__elevenlabs__text_to_speech",
    "mcp__elevenlabs__text_to_voice",
    "mcp__elevenlabs__voice_clone",
    "mcp__elevenlabs__create_voice_from_preview",
    "mcp__elevenlabs__compose_music",
    "mcp__elevenlabs__text_to_sound_effects",
    "mcp__elevenlabs__speech_to_speech",
}

# Bash command patterns hitting render APIs (POST/PUT, not GET status)
RENDER_BASH_PATTERNS = [
    re.compile(r"api\.kie\.ai.*-X\s+(POST|PUT)", re.IGNORECASE | re.DOTALL),
    re.compile(r"-X\s+(POST|PUT).*api\.kie\.ai", re.IGNORECASE | re.DOTALL),
    re.compile(r"higgsfield\.ai.*-X\s+(POST|PUT)", re.IGNORECASE | re.DOTALL),
    re.compile(r"-X\s+(POST|PUT).*higgsfield\.ai", re.IGNORECASE | re.DOTALL),
    re.compile(r"requests\.(post|put)\s*\(.*kie\.ai", re.IGNORECASE | re.DOTALL),
    re.compile(r"requests\.(post|put)\s*\(.*higgsfield", re.IGNORECASE | re.DOTALL),
]

# kie.ai serves BOTH chat/LLM (under /claude/ and /openai/) and media renders
# (under /api/) from the same host. The render patterns above match any POST to
# api.kie.ai — so a plain LLM call (kie_chat.py, provider verification curls)
# false-flags as a render. These discriminate: a command hitting a kie.ai chat
# path with no kie.ai /api/ render path is an LLM call, not a render.
# (S368 2026-05-20 — fixes ~8 false positives in one Hermes-wiring session.)
KIE_CHAT_PATH_RE = re.compile(
    r"api\.kie\.ai/(?:claude/|openai/)?v\d+/(?:messages|chat/completions)",
    re.IGNORECASE,
)
KIE_RENDER_PATH_RE = re.compile(r"api\.kie\.ai/api/", re.IGNORECASE)


def iter_tool_uses(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") != "assistant":
                    continue
                msg = evt.get("message", {})
                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        yield blk.get("name", ""), blk.get("input", {}) or {}
    except (OSError, UnicodeDecodeError):
        return


def detect_render_activity(transcript_path):
    """Return (touched_render, touched_queue, evidence) for this session."""
    touched_render = False
    touched_queue = False
    evidence = None

    for name, tin in iter_tool_uses(transcript_path):
        # Render MCP write tools
        if name in RENDER_MCP_WRITE_TOOLS:
            touched_render = True
            evidence = name
            continue

        # Render-targeted Bash POST/PUT
        if name == "Bash":
            cmd = str(tin.get("command", ""))
            # kie.ai chat/LLM calls share the host with render endpoints —
            # exclude them: a kie.ai chat path with no kie.ai /api/ render path
            # is an LLM call, not a render. (S368 2026-05-20 false-positive fix.)
            kie_chat_only = bool(
                KIE_CHAT_PATH_RE.search(cmd)
                and not KIE_RENDER_PATH_RE.search(cmd)
                and "higgsfield" not in cmd.lower()
            )
            if not kie_chat_only:
                for pat in RENDER_BASH_PATTERNS:
                    if pat.search(cmd):
                        touched_render = True
                        evidence = "Bash POST/PUT to render API"
                        break

        # Edit/Write to render-queue.md
        if name in ("Edit", "Write", "NotebookEdit"):
            fp = str(tin.get("file_path", ""))
            if RENDER_QUEUE_FILE in fp:
                touched_queue = True

    return touched_render, touched_queue, evidence


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    if os.environ.get("SENTINEL_OVERRIDE", "").lower() == "render-queue":
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)

    transcript_path = data.get("transcript_path", "")
    touched_render, touched_queue, evidence = detect_render_activity(transcript_path)

    if not touched_render:
        sys.exit(0)

    if touched_queue:
        # Phase 3 dual-write (S358 2026-05-15): refresh asset-registry.md so
        # built-but-unshipped flags stay current without operator intervention.
        # Idempotent backfill; failure is non-blocking.
        try:
            cwd = os.getcwd()
            backfill_path = os.path.join(cwd, ASSET_REGISTRY_BACKFILL)
            if os.path.exists(backfill_path):
                subprocess.run(
                    ["python3", backfill_path],
                    timeout=30,
                    capture_output=True,
                    check=False,
                )
        except (subprocess.TimeoutExpired, OSError):
            pass
        sys.exit(0)

    msg = (
        f"[RENDER QUEUE GATE] Render activity detected this session "
        f"({evidence}) but `echo/render-queue.md` was NOT edited.\n\n"
        f"Renders are the highest dollar exposure + most opaque drift class. "
        f"If a render hangs or fails mid-batch with no repo trace, $ burned "
        f"and the asset is invisible to the next session.\n\n"
        f"PREFERRED: Pre-tag pattern (log as render moves through pipeline):\n"
        f"  1. BEFORE render call → add row tagged `PENDING` "
        f"(content ID, tool, prompt-hash, cost-est, requested-at)\n"
        f"  2. Run the render\n"
        f"  3. While running → swap PENDING → `RENDERING` "
        f"(task_id from API)\n"
        f"  4. AFTER success → swap RENDERING → `COMPLETE` "
        f"(file path, actual cost, runtime)\n"
        f"  5. On failure → swap to `FAILED` (error, retry-decision)\n"
        f"  Why: crash-safe, cost-tracked, audit-trailable. "
        f"Mirrors LAW 5 Two-Phase + LAW 16 Completion Depth.\n\n"
        f"FALLBACK: Post-log pattern (faster, loses intent on crash):\n"
        f"  Edit `echo/render-queue.md` with the completed render row.\n\n"
        f"Override (this turn only): SENTINEL_OVERRIDE=render-queue"
    )

    output = {
        "decision": "block",
        "reason": msg,
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
