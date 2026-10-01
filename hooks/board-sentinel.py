#!/usr/bin/env python3
"""Board Sentinel — PostToolUse hook for passive board member monitoring.

Detects when a tool call enters a board member's domain and injects
a 1-2 line advisory. Three severity levels:
  PASS  — quiet (no output unless VERBOSE)
  FLAG  — Echo must read and fix inline or invoke full skill
  ESCALATE — Echo MUST invoke the indicated skill before proceeding

FLAG messages trigger Echo to use AskUserQuestion when the issue
requires the operator's decision. See CLAUDE.md Protocol 9.

Override log: .claude/hooks/sentinel-override-log.json
"""

import json
import sys
import os
from datetime import datetime

# Quiet mode: only speak when something flags. Set True for pass messages too.
VERBOSE = False

# AUTONOMOUS tier actions — board member may resolve without escalation
AUTONOMOUS_PATTERNS = {
    "CMO": ["schedule post", "update rulebook", "flag underperforming", "rotate hook", "query atoms"],
    "CSO": ["update funnel stage", "adjust manychat", "log conversion", "flag broken checkout"],
    "CTO": [
        "restart mcp", "rotate api key", "fix broken integration", "update credential", "clear cache",
        "reindex library", "rebuild atoms", "backfill atoms", "run /ask", "update library",
    ],
    "GC": ["flag violation", "block non-compliant", "update disclosure", "log compliance"],
    "CFO": ["log transaction", "update subscription", "categorize expense", "flag budget", "log atoms usage"],
}

OVERRIDE_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sentinel-override-log.json")

# --- Domain Detection Rules ---
# Order matters: GC file paths checked before CTO to prevent .claude/ steal

DOMAINS = {
    "GC": {
        "file_paths": [
            ".claude/rules/publishing.md",
            ".claude/rules/",
            ".claude/hooks/violation-log.json",
        ],
        "mcp_prefixes": [],
        "bash_patterns": [
            "metricool.com/api/v2/scheduler/posts",
        ],
        "keywords": [
            "affiliate", "commission", "disclosure",
            "ftc", "paid partnership", "income claim",
            "guarantee", "#ad", "paid_partnership",
        ],
        "label": "GC",
    },
    "CSO": {
        "file_paths": [
            "memory/product-knowledge.md",
            "docs/_active/cso-brief.md",
        ],
        "mcp_prefixes": [
            "mcp__manychat__",
        ],
        "bash_patterns": [
            "manychat.com",
            "stan.store",
        ],
        "keywords": [
            "funnel", "conversion", "cta performance",
            "checkout", "revenue", "affiliate commission",
            "dm trigger", "keyword trigger",
        ],
        "label": "CSO",
    },
    "CMO": {
        "file_paths": [
            "drafts/",
            "content-plan.md",
            "docs/_active/content-log.md",
            "docs/_active/cmo-brief.md",
            "docs/_active/content-rulebook.md",
            "memory/patterns.md",
            "memory/competitor-intel.md",
            "memory/content-science.md",
            "memory/platform-playbooks.md",
        ],
        "mcp_prefixes": [
            "mcp__xpoz-mcp__getInstagram",
            "mcp__xpoz-mcp__getTiktok",
            "mcp__xpoz-mcp__searchInstagram",
            "mcp__xpoz-mcp__searchTiktok",
            "mcp__youtube__youtube_analytics_",
            "mcp__youtube__youtube_list_",
            "mcp__youtube__youtube_search",
            "mcp__kie-ai__nano_banana",
            "mcp__kie-ai__veo3_generate",
            "mcp__kie-ai__kling_avatar",
            "mcp__kie-ai__seedance",
            "mcp__kie-ai__hailuo",
            "mcp__kie-ai__wan_video",
            "mcp__kie-ai__runway",
            "mcp__kie-ai__sora",
            "mcp__kie-ai__suno",
            "mcp__elevenlabs__text_to_speech",
            "mcp__elevenlabs__speech_to_speech",
        ],
        "bash_patterns": [
            "metricool.com/api/v2/analytics",
        ],
        "keywords": [
            "engagement", "retention", "hook performance",
            "audience", "competitor", "growth rate",
            "skip rate", "save rate",
        ],
        "label": "CMO",
    },
    "CTO": {
        "file_paths": [
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".mcp.json",
            "docs/_active/cto-brief.md",
            "memory/tool-routing.md",
            "memory/tool-audit-tracker.md",
            "docs/_active/tool-intel-backlog.md",
            "memory/credential-inventory.md",
            "memory/freshness-tracker.md",
            ".claude/hooks/",
        ],
        "mcp_prefixes": [],
        "bash_patterns": [
            "pip install",
            "pip3 install",
            "npm install",
            "mcp-server",
            "brew install",
        ],
        "keywords": [
            "integration", "integrate", "mcp server", "api token",
            "credential", "config stale", "token expired",
            "auth failed", "connection refused",
            "/token", "token/",
            "tool-routing", "tool-intel", "deprecated",
            "diagnostic", "domain health", "tier score",
            "system health", "autolevel", "self-healing",
        ],
        "label": "CTO",
    },
    "CFO": {
        "file_paths": [
            "docs/_active/cfo-brief.md",
        ],
        "mcp_prefixes": [],
        "bash_patterns": [
            "subscription",
            "billing",
        ],
        "keywords": [
            "budget", "spend", "cost", "subscription",
            "monthly spend", "roi", "credit",
            "cancelled", "cancellation",
        ],
        "label": "CFO",
    },
}

# Priority for overlap resolution (last = highest priority)
CONTENT_PRIORITY = ["CFO", "CTO", "CMO", "CSO", "GC"]
INFRA_PRIORITY = ["CMO", "CSO", "GC", "CFO", "CTO"]


def detect_infra_context(tool_name, text):
    """Determine if this is an infrastructure operation (vs content)."""
    infra_signals = [".claude/", ".mcp.json", "settings.json", "pip ", "npm ", "mcp"]
    combined = (tool_name + " " + text).lower()
    return any(s in combined for s in infra_signals)


def check_domain(domain_rules, tool_name, file_path, command, tool_input_str):
    """Check if a tool call matches a board member's domain. Returns match reason or None."""
    # Check file paths (Edit/Write tools only — Read is observation, not action)
    if file_path and tool_name in ("Edit", "Write"):
        for pattern in domain_rules["file_paths"]:
            if pattern in file_path:
                short_name = file_path.split('/')[-1]
                return f"editing {pattern.rstrip('/')}/{short_name}" if pattern.endswith('/') else f"editing {short_name}"

    # Check MCP tool prefixes
    if tool_name:
        for prefix in domain_rules["mcp_prefixes"]:
            if tool_name.startswith(prefix):
                return f"MCP tool {tool_name.split('__')[-1]}"

    # Check bash command patterns
    if command:
        cmd_lower = command.lower()
        for pattern in domain_rules["bash_patterns"]:
            if pattern in cmd_lower:
                return f"bash: {pattern}"

    # Check keywords in full tool input (Edit/Write/Bash/Agent only — not Read/Get/List)
    if tool_input_str and tool_name in ("Edit", "Write", "Bash", "Agent", ""):
        input_lower = tool_input_str.lower()
        for kw in domain_rules["keywords"]:
            # Word boundary check: keyword must not be part of a larger word
            # e.g., "ftc" shouldn't match "ftc-backup.md" filename in a Read
            idx = input_lower.find(kw)
            if idx >= 0:
                before = input_lower[idx - 1] if idx > 0 else " "
                after = input_lower[idx + len(kw)] if idx + len(kw) < len(input_lower) else " "
                if not before.isalnum() and not after.isalnum():
                    return f"keyword: {kw}"

    return None


def generate_flag(label, reason):
    """Generate sentinel message with severity level. Returns (severity, message) or None."""
    reason_lower = reason.lower()

    if label == "GC":
        if "scheduler" in reason_lower or "bash:" in reason_lower:
            return ("ESCALATE", f"[GC sentinel] ESCALATE — Publishing detected. Verify FTC disclosure. Run /gc review before proceeding.")
        if "editing" in reason_lower:
            return ("FLAG", f"[GC sentinel] FLAG — Compliance file change ({reason}). Verify rules still enforced.")
        if any(kw in reason_lower for kw in ["affiliate", "commission", "disclosure", "ftc", "income", "#ad", "paid", "guarantee"]):
            return ("FLAG", f"[GC sentinel] FLAG — Compliance keyword ({reason}). Check disclosure requirements.")

    if label == "CSO":
        if "manychat" in reason_lower:
            return ("FLAG", f"[CSO sentinel] FLAG — ManyChat territory. Check: are flows converting? /cso manychat if needed.")
        if "stan" in reason_lower:
            return ("FLAG", f"[CSO sentinel] FLAG — Stan Store activity. Checkout flow healthy? /cso stan if needed.")
        if "editing" in reason_lower:
            return ("PASS", f"[CSO sentinel] Sales knowledge updated ({reason}).")
        if any(kw in reason_lower for kw in ["funnel", "conversion", "checkout", "revenue", "dm trigger", "keyword trigger"]):
            return ("FLAG", f"[CSO sentinel] FLAG — Sales domain active ({reason}). Is the funnel path clear?")

    if label == "CMO":
        # Content CREATION tools — FLAG to ensure CMO brief + storyboard alignment
        if any(kw in reason_lower for kw in ["nano_banana", "veo3", "kling_avatar", "seedance", "hailuo", "wan_video", "runway", "sora"]):
            return ("FLAG", f"[CMO sentinel] FLAG — Content generation ({reason}). Aligned with CMO brief + storyboard? Pipeline matches proven workflow?")
        if any(kw in reason_lower for kw in ["text_to_speech", "speech_to_speech", "elevenlabs"]):
            return ("FLAG", f"[CMO sentinel] FLAG — Voice generation ({reason}). Script approved? Voice settings match feedback (speed 0.85, similarity 0.65)?")
        if "drafts" in reason_lower or "content" in reason_lower:
            return ("PASS", f"[CMO sentinel] Content in play. DSB/CC applied? CTA aligned with platform?")
        if "editing" in reason_lower:
            return ("PASS", f"[CMO sentinel] Marketing data updated ({reason}).")
        if any(kw in reason_lower for kw in ["instagram", "tiktok", "analytics", "audience", "competitor", "youtube", "engagement", "retention", "skip", "save"]):
            return ("PASS", f"[CMO sentinel] Audience data ({reason}). Is it moving them toward purchase?")

    if label == "CTO":
        if "deprecated" in reason_lower:
            return ("FLAG", f"[CTO sentinel] FLAG — Deprecated tool referenced ({reason}). Check tool-routing.md for replacement.")
        if "tool-intel" in reason_lower or "tool-routing" in reason_lower:
            return ("PASS", f"[CTO sentinel] Tool intelligence activity ({reason}).")
        if "editing" in reason_lower:
            return ("PASS", f"[CTO sentinel] Infra change ({reason}). Test after modifying.")
        if "bash:" in reason_lower:
            return ("FLAG", f"[CTO sentinel] FLAG — Package/tool install ({reason}). Verify it works.")
        if any(kw in reason_lower for kw in ["token", "credential", "auth", "connection"]):
            return ("FLAG", f"[CTO sentinel] FLAG — Security-adjacent change ({reason}). Verify no secrets exposed.")
        if any(kw in reason_lower for kw in ["integrat", "mcp", "config"]):
            return ("PASS", f"[CTO sentinel] Infra activity ({reason}). Systems healthy?")

    if label == "CFO":
        if "editing" in reason_lower:
            return ("PASS", f"[CFO sentinel] Finance data updated ({reason}).")
        if any(kw in reason_lower for kw in ["budget", "spend", "cost", "subscription", "roi", "credit"]):
            return ("FLAG", f"[CFO sentinel] FLAG — Financial activity ({reason}). Within budget?")

    return None


def log_override(domain, reason, action="proceeded"):
    """Write to sentinel override log when Echo bypasses a FLAG."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "domain": domain,
        "reason": reason,
        "action": action,
    }
    try:
        if os.path.exists(OVERRIDE_LOG):
            with open(OVERRIDE_LOG, "r") as f:
                log = json.load(f)
        else:
            log = []
        log.append(entry)
        with open(OVERRIDE_LOG, "w") as f:
            json.dump(log, f, indent=2)
    except (json.JSONDecodeError, PermissionError, OSError):
        pass  # Don't crash the hook on log write failure


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if not tool_name:
        sys.exit(0)

    # Extract signals from tool input
    file_path = ""
    command = ""
    tool_input_str = json.dumps(tool_input).lower() if tool_input else ""

    if isinstance(tool_input, dict):
        file_path = tool_input.get("file_path", "") or ""
        command = tool_input.get("command", "") or ""
        # Agent tool: treat prompt as command for bash_pattern scanning
        if tool_name == "Agent" and not command:
            command = tool_input.get("prompt", "") or ""

    # Check each domain (order matters — GC before CTO for .claude/ paths)
    matches = {}
    for domain_key, rules in DOMAINS.items():
        reason = check_domain(rules, tool_name, file_path, command, tool_input_str)
        if reason:
            matches[domain_key] = reason

    if not matches:
        sys.exit(0)

    # Overlap resolution: max 2 sentinels
    if len(matches) > 2:
        is_infra = detect_infra_context(tool_name, tool_input_str)
        priority = INFRA_PRIORITY if is_infra else CONTENT_PRIORITY
        sorted_matches = sorted(matches.keys(), key=lambda k: priority.index(k), reverse=True)
        matches = {k: matches[k] for k in sorted_matches[:2]}

    # Cross-department tagging: if multiple domains matched, note the overlap
    cross_dept = len(matches) > 1

    # Generate output
    output_lines = []
    for domain_key, reason in matches.items():
        label = DOMAINS[domain_key].get("label", domain_key)

        # Check if this is an AUTONOMOUS tier action
        is_autonomous = False
        reason_lower_check = reason.lower()
        for pattern in AUTONOMOUS_PATTERNS.get(domain_key, []):
            if pattern in reason_lower_check:
                is_autonomous = True
                break

        result = generate_flag(label, reason)
        if result:
            severity, message = result
            # Add AUTONOMOUS tier annotation
            if is_autonomous and severity == "FLAG":
                message += " [AUTONOMOUS — board member may resolve without escalation]"
            # Add cross-department tag
            if cross_dept:
                other_domains = [d for d in matches.keys() if d != domain_key]
                message += f" [also: {', '.join(other_domains)}]"
            if severity in ("FLAG", "ESCALATE"):
                output_lines.append(message)
            elif VERBOSE:
                output_lines.append(message)

    if output_lines:
        print("\n".join(output_lines))

    sys.exit(0)


if __name__ == "__main__":
    main()
