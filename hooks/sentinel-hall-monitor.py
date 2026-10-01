#!/usr/bin/env python3
"""Sentinel Hall Monitor — PreToolUse hook for redundancy blocking.

Greps 5 state sources before domain-entering tool calls. Blocks (exit 2)
if the topic is already verified, decided, or resolved. Blunt. Non-advisory.

Sources checked:
  1. MEMORY.md — VERIFIED, WORKING, LIVE, CONFIRMED, DEAD markers
  2. decisions.md — logged decisions
  3. Latest handoff — "What Got Done" section
  4. FLAGS.md — RESOLVED flags
  5. Transcripts — current + previous session

Override: Set env SENTINEL_OVERRIDE=topic to bypass for that topic.
"""

import json
import sys
import os
import re
import glob
from datetime import datetime, timedelta

# --- Paths ---
HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HOOKS_DIR)  # .claude/
REPO_DIR = os.path.dirname(PROJECT_DIR)   # repo root

# Derive auto-memory path dynamically from repo location
_project_hash = REPO_DIR.replace("/", "-")
MEMORY_DIR = os.path.expanduser(
    f"~/.claude/projects/{_project_hash}/memory"
)
# Fall back to repo memory/ if auto-memory path doesn't exist
if not os.path.isdir(MEMORY_DIR):
    MEMORY_DIR = os.path.join(REPO_DIR, "memory")
MEMORY_MD = os.path.join(MEMORY_DIR, "MEMORY.md")
DECISIONS_MD = os.path.join(MEMORY_DIR, "decisions.md")
FLAGS_MD = os.path.join(REPO_DIR, "echo", "mac-reports", "FLAGS.md")
HANDOFF_DIR = os.path.join(REPO_DIR, "echo", "handoffs")
TRANSCRIPT_DIR = os.path.join(REPO_DIR, "echo", "transcripts")

STALENESS_DAYS = 7

# --- Exempt tools (observation only) ---
EXEMPT_TOOLS = {
    "Read", "Glob", "Grep", "TodoWrite", "AskUserQuestion",
    "EnterPlanMode", "ExitPlanMode", "ToolSearch",
}

# --- Diagnostic bypass ---
# When Echo is running /diagnostic, sentinel should not block investigation
# of systems being audited. Override via env or detect diagnostic context.
DIAGNOSTIC_BYPASS = os.environ.get("DIAGNOSTIC_MODE", "") == "1"

# --- State markers ---
STATE_MARKERS = re.compile(
    r"VERIFIED|WORKING|LIVE|CONFIRMED|DEAD|RESOLVED|CANCELLED",
    re.IGNORECASE,
)
RESOLVED_MARKERS = re.compile(
    r"RESOLVED|DEAD|not actioned|already decided|cancelled",
    re.IGNORECASE,
)
TRANSCRIPT_MARKERS = re.compile(
    r"verified|confirmed|decided|approved|working|resolved",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"20\d{2}-\d{2}-\d{2}")

# --- Topic extraction from tool calls ---
MCP_TOPIC_MAP = {
    "mcp__manychat__": "manychat",
    "mcp__xpoz-mcp__getInstagram": "instagram",
    "mcp__xpoz-mcp__getTiktok": "tiktok",
    "mcp__xpoz-mcp__searchInstagram": "instagram",
    "mcp__xpoz-mcp__searchTiktok": "tiktok",
    "mcp__xpoz-mcp__getTwitter": "twitter",
    "mcp__xpoz-mcp__getReddit": "reddit",
    "mcp__youtube__": "youtube",
    "mcp__elevenlabs__": "elevenlabs",
    "mcp__kie-ai__": "kie-ai",
    "mcp__workspace-mcp__": "google workspace",
    "mcp__notebooklm__": "notebooklm",
    "mcp__b1a3f1f9": "canva",
}

BASH_TOPIC_MAP = {
    "metricool.com": "metricool",
    "manychat.com": "manychat",
    "stan.store": "stan store",
    "elevenlabs": "elevenlabs",
    "kie-ai": "kie-ai",
}

# Known system names to scan in tool input text
KNOWN_TOPICS = [
    "manychat", "metricool", "stan store", "youtube", "tiktok",
    "instagram", "elevenlabs", "kie-ai", "canva", "notion",
    "bs1-7", "bs1", "bs2", "bs3", "bs4", "bs5", "bs6", "bs7",
]


def classify_intent(tool_name, tool_input):
    """Classify tool call intent: verify, research, or data.
    
    - verify: re-checking status of a known system (block-eligible)
    - research: new investigation or analysis (allow)
    - data: processing, classification, scripting (allow)
    """
    command = ""
    if isinstance(tool_input, dict):
        command = tool_input.get("command", "") or ""
        if tool_name == "Agent" and not command:
            command = tool_input.get("prompt", "") or ""
    command_lower = command.lower()

    # Git operations: always data (local filesystem, never re-verification)
    if tool_name == "Bash" and command_lower:
        git_signals = [
            command_lower.startswith("git "),
            command_lower.startswith("git\t"),
            "| git " in command_lower,
            "&& git " in command_lower,
        ]
        if any(git_signals):
            return "data"

    # Edit/Write: content being written is data output, not verification
    # The file PATH matters for zone-guard, but the CONTENT is never verification
    if tool_name in ("Edit", "Write"):
        return "data"

    # Data processing: heredoc scripts, python, file transforms
    data_signals = [
        "<<" in command,
        "python" in command_lower and (".py" in command_lower or "import" in command_lower),
        "node " in command_lower and ".js" in command_lower,
        "classify" in command_lower,
        "categoriz" in command_lower,
        "parse" in command_lower,
        "json.load" in command_lower,
        "csv" in command_lower and "import" in command_lower,
        "jq " in command_lower,
    ]
    if any(data_signals):
        return "data"

    # API GET requests fetching current state = research (not re-verification)
    # These pull live data — the opposite of "already verified"
    if tool_name == "Bash" and "curl" in command_lower:
        api_fetch_signals = [
            "scheduler/posts" in command_lower,
            "-x get" in command_lower and "status" in command_lower,
            "scheduled" in command_lower,
        ]
        if any(api_fetch_signals):
            return "research"

    # API POST/PUT requests creating new resources = data (not re-verification)
    # e.g., cloning a voice, uploading media, creating content
    if tool_name == "Bash" and "curl" in command_lower:
        api_create_signals = [
            "-x post" in command_lower,
            "-f " in command_lower,  # multipart form upload
            "voices/add" in command_lower,
            "upload" in command_lower and "post" not in command_lower,
        ]
        if any(api_create_signals):
            return "data"

    # Inbox API calls (comment replies, DM reads) = data output, not verification
    if tool_name == "Bash" and "/inbox/" in command_lower:
        return "data"

    # Write/Edit to documentation/logging paths = data output (not verification)
    if tool_name in ("Write", "Edit"):
        path = (tool_input.get("file_path", "") or "").lower()
        if any(seg in path for seg in ["/tmp/", "/downloads/", "/echo - out/",
                                        "/echo-business/", "/drafts/",
                                        "/handoffs/", "/transcripts/",
                                        "/mac-reports/", "/intake/",
                                        "/memory/", "/projects/",
                                        "/hooks/", "/plans/", "/skills/",
                                        "content-log", "queue-status",
                                        "active-work", "backlog", "dream-log"]):
            return "data"

    # Research signals in Agent prompts
    research_kws = ["research", "analyze", "scrape", "find all", "categorize",
                    "classify", "compare", "competitor", "trending", "audit",
                    "pull data", "fetch all", "export", "inventory", "catalog"]
    if tool_name == "Agent" and any(s in command_lower for s in research_kws):
        return "research"

    # Verify signals: explicitly re-checking known state
    verify_kws = ["is it working", "still live", "still working",
                  "test connection", "still connected", "still responding",
                  "sanity check", "recheck", "re-check"]
    if any(s in command_lower for s in verify_kws):
        return "verify"
    if "check if" in command_lower and "still" in command_lower:
        return "verify"
    if "verify" in command_lower and any(w in command_lower for w in
            ["status", "working", "connected", "live", "running", "is"]):
        return "verify"
    if "been a while" in command_lower and any(w in command_lower for w in
            ["verify", "check", "test", "confirm"]):
        return "verify"

    # MCP generative/creative actions = data output (not verification)
    # These create new content — blocking them on prior state is a false positive.
    MCP_GENERATIVE_SUFFIXES = (
        "text_to_voice", "text_to_speech", "text_to_sound_effects",
        "speech_to_speech", "speech_to_text", "create_voice_from_preview",
        "voice_clone", "compose_music", "create_composition_plan",
        "generate_video", "generate_image", "generate_design",
        "generate_design_structured", "generate_content",
        "grok_imagine", "midjourney_generate", "flux2_image",
        "flux_kontext_image", "nano_banana_image", "seedance_video",
        "seedream_image", "kling_video", "kling_avatar", "wan_video",
        "wan_animate", "veo3_generate_video", "sora_video",
        "hailuo_video", "runway_aleph_video", "suno_generate_music",
        "ideogram_reframe", "z_image", "qwen_image", "openai_4o_image",
        "create_agent", "create_design", "import_design_from_url",
        "infinitalk_lip_sync",
        # YouTube (write ops on mixed server)
        "youtube_upload_video", "youtube_update_video", "youtube_delete_video",
        "youtube_post_comment", "youtube_reply_to_comment",
        "youtube_set_thumbnail", "youtube_create_playlist",
        "youtube_add_to_playlist", "youtube_remove_from_playlist",
        "youtube_reporting_create_job",
        # ManyChat (write ops)
        "manychat_send_content", "manychat_send_flow", "manychat_set_field",
        "manychat_tag_subscriber", "manychat_untag_subscriber",
        "manychat_create_tag",
        # Xpoz (tracking ops)
        "addTrackedItems", "removeTrackedItems",
        # Gmail (write ops)
        "gmail_create_draft",
        # Google Calendar (write ops)
        "gcal_create_event", "gcal_update_event", "gcal_delete_event",
        "gcal_respond_to_event",
        # Canva / auth
        "authenticate",
        # Google Workspace (write ops — content uploads contain tool name keywords)
        "import_to_google_doc", "create_drive_file", "update_drive_file",
        "create_spreadsheet", "update_spreadsheet",
    )
    tool_suffix = tool_name.split("__")[-1] if "__" in tool_name else ""
    if tool_name.startswith("mcp__") and tool_suffix in MCP_GENERATIVE_SUFFIXES:
        return "data"

    # MCP read-only / query actions = research (not verification)
    MCP_QUERY_SUFFIXES = (
        # ElevenLabs
        "search_voices", "list_voices", "get_voice", "list_models",
        "check_subscription", "get_agent", "list_agents",
        "get_conversation", "list_conversations",
        "search_voice_library", "get_task_status", "list_tasks",
        "play_audio", "isolate_audio",
        "list_phone_numbers",
        # YouTube (read-only subset of mixed server)
        "youtube_search", "youtube_get_video", "youtube_get_channel",
        "youtube_list_videos", "youtube_list_playlists",
        "youtube_list_comments", "youtube_list_captions",
        "youtube_get_transcript", "youtube_get_categories",
        "youtube_trending", "youtube_search_suggestions",
        "youtube_auth", "youtube_auth_status",
        "youtube_analytics_overview", "youtube_analytics_daily",
        "youtube_analytics_demographics", "youtube_analytics_geography",
        "youtube_analytics_traffic_sources", "youtube_analytics_top_videos",
        "youtube_analytics_top_shorts", "youtube_analytics_retention",
        "youtube_analytics_revenue", "youtube_analytics_revenue_by_video",
        "youtube_analytics_video_detail", "youtube_analytics_day_of_week",
        "youtube_analytics_content_type_breakdown",
        "youtube_reporting_list_jobs", "youtube_reporting_list_reports",
        "youtube_reporting_list_types", "youtube_reporting_download",
        # ManyChat (read-only subset)
        "manychat_page_info", "manychat_list_flows", "manychat_list_tags",
        "manychat_list_custom_fields", "manychat_list_growth_tools",
        "manychat_find_subscriber", "manychat_find_by_field",
        "manychat_get_subscriber",
        # Gmail (read-only subset — Claude AI Gmail MCP)
        "gmail_search_messages", "gmail_read_message", "gmail_read_thread",
        "gmail_list_labels", "gmail_list_drafts", "gmail_get_profile",
        # Google Calendar (read-only subset — Claude AI Calendar MCP)
        "gcal_list_events", "gcal_get_event", "gcal_list_calendars",
        "gcal_find_meeting_times", "gcal_find_my_free_time",
        # Google Workspace MCP (read-only subset — workspace-mcp)
        "search_gmail_messages", "get_gmail_message_content",
        "get_gmail_messages_content_batch",
        "search_drive_files", "get_drive_file_content",
        "get_drive_file_download_url", "get_drive_shareable_link",
        "read_sheet_values",
    )
    if tool_name.startswith("mcp__") and tool_suffix in MCP_QUERY_SUFFIXES:
        return "research"

    # Entire MCP servers where ALL tools are read-only research
    # (no state-changing actions — safe to classify wholesale)
    MCP_RESEARCH_SERVERS = {
        "xpoz-mcp",                              # Social data pulls
        "firecrawl",                             # Web scraping/search
        "notebooklm",                            # RAG queries
        "context-mode",                          # Context processing
        "plugin_context-mode_context-mode",      # Context processing (plugin variant)
        "scrivener",                             # Document reading
    }
    parts = tool_name.split("__")
    server = parts[1] if len(parts) >= 3 else ""
    if server in MCP_RESEARCH_SERVERS:
        return "research"

    # MCP tools not matched above = direct system interaction (block-eligible)
    if tool_name.startswith("mcp__"):
        return "verify"

    return "verify"


def extract_topics(tool_name, tool_input):
    """Extract topic keywords from a tool call. Returns set of topics.

    Context-aware: only keyword-scans script bodies when intent is verify.
    Generative/creative MCP actions skip all topic extraction (no false positives).
    """
    topics = set()

    # Generative/query MCP actions: skip all topic extraction — these create new
    # content or fetch live state. Blocking on prior state is a false positive.
    intent = classify_intent(tool_name, tool_input)
    if intent in ("data", "research") and tool_name.startswith("mcp__"):
        return topics  # Empty — nothing to block on

    # MCP tool prefix matching -- always extract
    for prefix, topic in MCP_TOPIC_MAP.items():
        if tool_name.startswith(prefix):
            topics.add(topic)

    # Classify intent before deciding whether to keyword-scan
    intent = classify_intent(tool_name, tool_input)
    if intent in ("data", "research"):
        return topics  # Only MCP-prefix topics, skip keyword scan

    # Below only runs for verify intent
    command = ""
    if isinstance(tool_input, dict):
        command = (tool_input.get("command", "") or "").lower()
        if tool_name == "Agent" and not command:
            command = (tool_input.get("prompt", "") or "").lower()

    if command:
        for pattern, topic in BASH_TOPIC_MAP.items():
            if pattern in command:
                topics.add(topic)

    input_str = json.dumps(tool_input).lower() if tool_input else ""
    for topic in KNOWN_TOPICS:
        if topic in input_str:
            if topic.startswith("bs") and topic != "bs1-7":
                topics.add("bs1-7")
            else:
                topics.add(topic)

    return topics


def parse_date(text):
    """Extract the most recent date from text. Returns datetime or None."""
    dates = DATE_PATTERN.findall(text)
    if not dates:
        return None
    try:
        return max(datetime.strptime(d, "%Y-%m-%d") for d in dates)
    except ValueError:
        return None


def is_stale(date_obj):
    """Check if a date is older than STALENESS_DAYS."""
    if date_obj is None:
        return False  # Can't parse date = treat as fresh (block)
    return (datetime.now() - date_obj).days > STALENESS_DAYS


def read_file_safe(path, max_lines=300):
    """Read a file safely, return lines or empty list."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line)
            return lines
    except (FileNotFoundError, PermissionError, OSError):
        return []


def grep_nearby(lines, topic, marker_re, window=3):
    """Check if topic appears within `window` lines of a marker match.
    Returns (matched: bool, state: str, context: str, date: datetime|None).
    """
    topic_lower = topic.lower()
    topic_lines = []
    marker_lines = []

    for i, line in enumerate(lines):
        if topic_lower in line.lower():
            topic_lines.append(i)
        if marker_re.search(line):
            marker_lines.append((i, line.strip()))

    for t_line in topic_lines:
        for m_line, m_text in marker_lines:
            if abs(t_line - m_line) <= window:
                # Extract state word
                match = marker_re.search(m_text)
                state = match.group(0) if match else "RESOLVED"
                # Extract date from nearby lines
                context_start = max(0, min(t_line, m_line) - 1)
                context_end = min(len(lines), max(t_line, m_line) + 2)
                context_text = "".join(lines[context_start:context_end])
                date = parse_date(context_text)
                return True, state, m_text[:80], date

    return False, "", "", None


def check_memory(topic):
    """Check MEMORY.md for resolved state."""
    lines = read_file_safe(MEMORY_MD)
    if not lines:
        return None
    matched, state, context, date = grep_nearby(lines, topic, STATE_MARKERS)
    if matched and not is_stale(date):
        date_str = date.strftime("%Y-%m-%d") if date else "recent"
        return f"{state} (MEMORY.md, {date_str})"
    return None


def check_decisions(topic):
    """Check decisions.md for logged decisions."""
    lines = read_file_safe(DECISIONS_MD)
    if not lines:
        return None
    # Look for topic in decision headings and nearby text
    matched, state, context, date = grep_nearby(
        lines, topic, re.compile(r"Decision:|Decided|Keeping|Cancelled|DEAD", re.IGNORECASE)
    )
    if matched and not is_stale(date):
        date_str = date.strftime("%Y-%m-%d") if date else "recent"
        return f"DECIDED (decisions.md, {date_str})"
    return None


def check_handoff(topic):
    """Check latest handoff for 'What Got Done' mentions."""
    try:
        files = sorted(glob.glob(os.path.join(HANDOFF_DIR, "*.md")), reverse=True)
    except OSError:
        return None
    if not files:
        return None

    lines = read_file_safe(files[0])
    if not lines:
        return None

    # Only scan "What Got Done" section
    in_section = False
    section_lines = []
    for line in lines:
        if "What Got Done" in line or "What got done" in line:
            in_section = True
            continue
        if in_section:
            if line.startswith("## ") and "What" not in line:
                break
            section_lines.append(line)

    if not section_lines:
        return None

    topic_lower = topic.lower()
    for line in section_lines:
        if topic_lower in line.lower():
            fname = os.path.basename(files[0])
            date = parse_date(fname)
            if date and not is_stale(date):
                return f"DONE in handoff ({fname})"
    return None


def check_flags(topic):
    """Check FLAGS.md for resolved flags."""
    lines = read_file_safe(FLAGS_MD)
    if not lines:
        return None
    matched, state, context, date = grep_nearby(lines, topic, RESOLVED_MARKERS)
    if matched and not is_stale(date):
        date_str = date.strftime("%Y-%m-%d") if date else "recent"
        return f"RESOLVED (FLAGS.md, {date_str})"
    return None


def check_transcripts(topic):
    """Check current + previous session transcripts."""
    try:
        files = sorted(glob.glob(os.path.join(TRANSCRIPT_DIR, "*.md")), reverse=True)
    except OSError:
        return None
    # Current + previous session = up to 2 files
    files = files[:2]
    if not files:
        return None

    for fpath in files:
        lines = read_file_safe(fpath, max_lines=500)
        if not lines:
            continue
        matched, state, context, date = grep_nearby(
            lines, topic, TRANSCRIPT_MARKERS, window=5
        )
        if matched and not is_stale(date):
            fname = os.path.basename(fpath)
            return f"{state} (transcript {fname})"
    return None


def check_override(topic):
    """Check if SENTINEL_OVERRIDE env var matches this topic."""
    override = os.environ.get("SENTINEL_OVERRIDE", "").lower()
    if not override:
        return False
    return topic.lower() in override or override in topic.lower()


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # Exempt tools pass through
    if tool_name in EXEMPT_TOOLS:
        sys.exit(0)

    # Diagnostic mode bypass — Echo's authority during system audits
    if DIAGNOSTIC_BYPASS:
        sys.exit(0)

    # Extract topics
    topics = extract_topics(tool_name, tool_input)
    if not topics:
        sys.exit(0)

    # Check each topic against all sources
    blocks = []
    for topic in topics:
        if check_override(topic):
            continue

        # Check all 5 sources, stop at first hit per topic
        result = (
            check_memory(topic)
            or check_decisions(topic)
            or check_handoff(topic)
            or check_flags(topic)
            or check_transcripts(topic)
        )
        if result:
            blocks.append((topic, result))

    if blocks:
        reasons = []
        for topic, result in blocks:
            reasons.append(
                f"[SENTINEL HALT] {topic} already {result}. "
                f"State the NEW question or move on."
            )
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "\n".join(reasons),
            }
        }
        print(json.dumps(output))
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
