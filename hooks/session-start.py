#!/usr/bin/env python3
"""SessionStart hook: prints date, Sunday rule, due reminders, and stuck carryover items."""

import json
import os
import re
import sys
from datetime import datetime


def get_project_dir():
    return os.environ.get("CLAUDE_PROJECT_DIR", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def parse_reminders(path):
    """Parse all reminders.md tables. Flag past-due, due-today, CRITICAL, and TODAY items."""
    try:
        with open(path, "r") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError):
        return None

    flagged = []

    for line in content.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue

        cells = [c.strip().strip("*").strip() for c in line.split("|")]
        cells = [c for c in cells if c]

        if len(cells) < 2:
            continue

        # Skip header/separator rows
        first = cells[0].lower()
        if first in ("task", "---", "") or first.startswith("-") or first.startswith(":"):
            continue

        task = cells[0]
        row_text = " ".join(cells[1:])

        # Check for TODAY flag
        if "TODAY" in row_text.upper():
            flagged.append(f"  [TODAY] {task}")
            continue

        # Check for CRITICAL flag
        if "CRITICAL" in row_text.upper():
            flagged.append(f"  [CRITICAL] {task}")
            continue

        # Check for past due dates (YYYY-MM-DD format)
        date_matches = re.findall(r"(\d{4}-\d{2}-\d{2})", row_text)
        for date_str in date_matches:
            try:
                due_date = datetime.strptime(date_str, "%Y-%m-%d")
                if due_date.date() < datetime.now().date():
                    flagged.append(f"  [PAST DUE: {date_str}] {task}")
                    break
                elif due_date.date() == datetime.now().date():
                    flagged.append(f"  [DUE TODAY] {task}")
                    break
            except ValueError:
                pass

    return flagged if flagged else None


def parse_active_work_stuck(path):
    """Parse active-work.md (or any table file) and return rows with Sessions/Carried >= 3.

    Generic table parser — works on the column structure of active-work.md +
    backlog.md, replacing the original carryover-tracker.md target (DEPRECATED
    S224, superseded per docs/_active/source-registry.yml). Kept the parsing
    logic identical (column 3 = Sessions/Carried count) so behavior is stable.
    """
    try:
        with open(path, "r") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError):
        return None

    stuck = []

    for line in content.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue

        # Split on pipe and strip whitespace + bold markers
        raw = line.split("|")
        cells = [c.strip().strip("*").strip() for c in raw]
        # Remove empty strings from leading/trailing pipes
        while cells and not cells[0]:
            cells.pop(0)
        while cells and not cells[-1]:
            cells.pop()

        if len(cells) < 4:
            continue

        # Expected: [#, Item, Owner, Sessions, Added, Status, Notes]
        # or:       [#, Item, First, Carried, Owner, Status, Blocker]
        # cells[0] = row number, cells[1] = item name, cells[3] = carried/sessions count

        # Skip header/separator rows
        first = cells[0]
        if first in ("#", ":-:") or first.startswith("-") or first.startswith(":"):
            continue

        item = cells[1]
        if item.lower() in ("item", "---", ""):
            continue

        carried_str = cells[3]

        try:
            carried = int(carried_str.strip())
            if carried >= 3:
                stuck.append((carried, item))
        except (ValueError, IndexError):
            continue

    if not stuck:
        return None

    # Sort by carried count descending, take top 5
    stuck.sort(key=lambda x: x[0], reverse=True)
    return stuck[:5]


def check_hook_perms(project_dir):
    """Audit hooks in settings.json for POSIX exec bit. Returns banner warning if any missing +x.
    AI-MTG21-10 Option A — warn-only audit (S363 2026-05-18; Gemini 2.5 Pro spoke output integrated)."""
    settings_path = os.path.join(project_dir, ".claude", "settings.json")
    audit_log_path = os.path.join(project_dir, ".claude", "hooks", "hook-perm-audit-log.jsonl")
    missing_perms = []

    try:
        try:
            if not os.path.isfile(settings_path):
                settings = {}
            else:
                with open(settings_path, "r") as f:
                    settings = json.load(f)

            hook_scripts = set()
            for event_hooks in settings.get("hooks", {}).values():
                for matcher_group in event_hooks:
                    for hook in matcher_group.get("hooks", []):
                        if hook.get("type") != "command":
                            continue
                        command = hook.get("command", "")
                        if ".py" in command and ".claude/hooks/" in command:
                            script_path = command.replace("$CLAUDE_PROJECT_DIR", project_dir)
                            hook_scripts.add(os.path.abspath(script_path))

            unique_missing = set()
            for script_path in sorted(list(hook_scripts)):
                if os.path.isfile(script_path):
                    if not os.access(script_path, os.X_OK):
                        rel = os.path.relpath(script_path, project_dir).replace(os.path.sep, "/")
                        unique_missing.add(rel)
            missing_perms = sorted(list(unique_missing))

        except json.JSONDecodeError as e:
            return f"[hook-perm check skipped: settings.json parse error: {e}]"
        except Exception as e:
            return f"[hook-perm check skipped: unexpected error: {e}]"

        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "session_id": os.environ.get("CLAUDE_SESSION_ID"),
                "missing_count": len(missing_perms),
                "missing_files": missing_perms,
            }
            with open(audit_log_path, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            pass

        if not missing_perms:
            return None

        n = len(missing_perms)
        warning_lines = [f"⚠️  HOOK-PERM WARN: {n} hook(s) missing +x — silent exec failure risk"]
        for path in missing_perms:
            warning_lines.append(f"  - {path}")
        warning_lines.append("  Fix: chmod +x <files above>")
        return "\n".join(warning_lines)

    except Exception as e:
        return f"[hook-perm check skipped: fatal error: {e}]"


def run_vitals(project_dir):
    """Tier 1: 22 vital checks across 9 domains. File-based only, no API calls. <5 seconds."""
    passes = 0
    total = 23
    issues = []

    claude_md = os.path.join(project_dir, "CLAUDE.md")
    hooks_dir = os.path.join(project_dir, ".claude", "hooks")
    skills_dir = os.path.join(project_dir, ".claude", "skills")
    settings_path = os.path.join(project_dir, ".claude", "settings.json")
    active_dir = os.path.join(project_dir, "docs", "_active")
    # Derive auto-memory path dynamically from project_dir
    project_hash = project_dir.replace("/", "-")
    auto_memory = os.path.expanduser(
        f"~/.claude/projects/{project_hash}/memory"
    )
    repo_memory = os.path.join(project_dir, "memory")
    # Check auto-memory first, fall back to repo memory/MEMORY.md
    memory_md = os.path.join(auto_memory, "MEMORY.md")
    if not os.path.isfile(memory_md):
        memory_md = os.path.join(repo_memory, "MEMORY.md")

    # --- Domain 1: Kernel (3 checks) ---
    # V1: CLAUDE.md exists
    if os.path.isfile(claude_md):
        passes += 1
    else:
        issues.append("Kernel: CLAUDE.md missing")

    # V2: Protocol count = 10
    if os.path.isfile(claude_md):
        try:
            with open(claude_md, "r") as f:
                kernel = f.read()
            proto_count = len(re.findall(r"\*\*Protocol \d+:", kernel))
            if proto_count >= 10:
                passes += 1
            else:
                issues.append(f"Kernel: {proto_count}/10 protocols found")
        except Exception:
            issues.append("Kernel: CLAUDE.md unreadable")
    else:
        issues.append("Kernel: can't check protocols (no CLAUDE.md)")

    # V3: Zone tiers (4 tiers defined)
    if os.path.isfile(claude_md):
        try:
            zone_count = 0
            for label in ["Security", "Zone A", "Zone B", "Zone C"]:
                if label in kernel:
                    zone_count += 1
            if zone_count >= 4:
                passes += 1
            else:
                issues.append(f"Kernel: {zone_count}/4 zone tiers found")
        except Exception:
            issues.append("Kernel: zone tier check failed")
    else:
        issues.append("Kernel: can't check zones (no CLAUDE.md)")

    # --- Domain 2: Hooks (2 checks) ---
    # V4: All hook .py files from settings.json exist on disk
    hooks_ok = True
    if os.path.isfile(settings_path):
        try:
            with open(settings_path, "r") as f:
                settings = json.load(f)
            hook_scripts = set()
            for event_hooks in settings.get("hooks", {}).values():
                for matcher_group in event_hooks:
                    for hook in matcher_group.get("hooks", []):
                        cmd = hook.get("command", "")
                        if cmd.endswith(".py"):
                            script = cmd.replace("$CLAUDE_PROJECT_DIR", project_dir)
                            hook_scripts.add(script)
            missing_hooks = [s for s in hook_scripts if not os.path.isfile(s)]
            if not missing_hooks:
                passes += 1
            else:
                hooks_ok = False
                issues.append(f"Hooks: {len(missing_hooks)} script(s) missing from disk")
        except Exception:
            hooks_ok = False
            issues.append("Hooks: settings.json parse error")
    else:
        hooks_ok = False
        issues.append("Hooks: settings.json missing")

    # V5: No syntax errors in hook .py files
    if os.path.isdir(hooks_dir):
        syntax_errors = 0
        for fname in os.listdir(hooks_dir):
            if fname.endswith(".py"):
                fpath = os.path.join(hooks_dir, fname)
                try:
                    with open(fpath, "r") as f:
                        source = f.read()
                    compile(source, fpath, "exec")
                except SyntaxError:
                    syntax_errors += 1
        if syntax_errors == 0:
            passes += 1
        else:
            issues.append(f"Hooks: {syntax_errors} file(s) with syntax errors")
    else:
        issues.append("Hooks: hooks directory missing")

    # --- Domain 3: Skills (2 checks) ---
    # V6: All skill dirs have SKILL.md
    # V7: All SKILL.md have ## Exit Gate
    if os.path.isdir(skills_dir):
        missing_skill = 0
        missing_gate = 0
        for entry in os.listdir(skills_dir):
            skill_path = os.path.join(skills_dir, entry)
            if os.path.isdir(skill_path) and entry != "references":
                skill_md = os.path.join(skill_path, "SKILL.md")
                if not os.path.isfile(skill_md):
                    missing_skill += 1
                else:
                    try:
                        with open(skill_md, "r") as f:
                            content = f.read()
                        if "## Exit Gate" not in content:
                            missing_gate += 1
                    except Exception:
                        missing_gate += 1
        if missing_skill == 0:
            passes += 1
        else:
            issues.append(f"Skills: {missing_skill} dir(s) missing SKILL.md")
        if missing_gate == 0:
            passes += 1
        else:
            issues.append(f"Skills: {missing_gate} SKILL.md missing Exit Gate")
    else:
        issues.append("Skills: skills directory missing")

    # --- Domain 4: MCPs (2 checks) ---
    # V8: Deferred tool count > 0 (MCP config present)
    # Can't check deferred tools from Python — check for .mcp.json or settings permissions
    mcp_json = os.path.join(project_dir, ".mcp.json")
    if os.path.isfile(mcp_json):
        passes += 1
    else:
        # Fallback: check if settings.json has MCP permissions
        if os.path.isfile(settings_path):
            try:
                with open(settings_path, "r") as f:
                    s = json.load(f)
                perms = s.get("permissions", {}).get("allow", [])
                mcp_perms = [p for p in perms if p.startswith("mcp__")]
                if mcp_perms:
                    passes += 1
                else:
                    issues.append("MCPs: no MCP config found")
            except Exception:
                issues.append("MCPs: settings parse error")
        else:
            issues.append("MCPs: no MCP config found")

    # V9: Permissions not empty
    if os.path.isfile(settings_path):
        try:
            with open(settings_path, "r") as f:
                s = json.load(f)
            perms = s.get("permissions", {})
            if perms and (perms.get("allow") or perms.get("deny")):
                passes += 1
            else:
                issues.append("MCPs: permissions empty")
        except Exception:
            issues.append("MCPs: permissions check failed")
    else:
        issues.append("MCPs: settings.json missing")

    # --- Domain 5: Board (2 checks) ---
    brief_names = ["cmo-brief.md", "cso-brief.md", "cto-brief.md", "gc-brief.md", "cfo-brief.md"]
    # V10: All 5 briefs exist
    if os.path.isdir(active_dir):
        missing_briefs = [b for b in brief_names if not os.path.isfile(os.path.join(active_dir, b))]
        if not missing_briefs:
            passes += 1
        else:
            issues.append(f"Board: {len(missing_briefs)} brief(s) missing")
    else:
        issues.append("Board: docs/_active/ missing")

    # V11: Brief freshness (none older than 14 days, exempt inactive domains)
    now_ts = datetime.now().timestamp()
    fourteen_days = 14 * 86400
    if os.path.isdir(active_dir):
        stale_briefs = []
        for b in brief_names:
            bp = os.path.join(active_dir, b)
            if os.path.isfile(bp):
                age = now_ts - os.path.getmtime(bp)
                if age > fourteen_days:
                    stale_briefs.append(b.replace("-brief.md", "").upper())
        if not stale_briefs:
            passes += 1
        else:
            issues.append(f"Board: stale briefs: {', '.join(stale_briefs)}")
    else:
        issues.append("Board: can't check freshness (no docs/_active/)")

    # --- Domain 6: Memory (4 checks) ---
    # V12: MEMORY.md exists
    if os.path.isfile(memory_md):
        passes += 1
    else:
        issues.append("Memory: MEMORY.md missing")

    # V13: MEMORY.md <150 lines (Dewey index — pure pointers, no content)
    if os.path.isfile(memory_md):
        try:
            with open(memory_md, "r") as f:
                line_count = sum(1 for _ in f)
            if line_count < 150:
                passes += 1
            else:
                issues.append(f"Memory: MEMORY.md is {line_count} lines (max 150 for Dewey index)")
        except Exception:
            issues.append("Memory: MEMORY.md unreadable")
    else:
        issues.append("Memory: can't check size (no MEMORY.md)")

    # V14: All links resolve
    if os.path.isfile(memory_md):
        try:
            with open(memory_md, "r") as f:
                md_content = f.read()
            refs = re.findall(r"\]\(([^)]+\.md)\)", md_content)
            orphans = 0
            for ref in refs:
                if ref.startswith("http") or ref.startswith("#"):
                    continue
                if not os.path.exists(os.path.join(auto_memory, ref)):
                    orphans += 1
            if orphans == 0:
                passes += 1
            else:
                issues.append(f"Memory: {orphans} orphaned link(s) in MEMORY.md")
        except Exception:
            issues.append("Memory: link check failed")
    else:
        issues.append("Memory: can't check links (no MEMORY.md)")

    # V15: No split-brain (auto-memory and repo in sync)
    if os.path.isdir(repo_memory) and os.path.isdir(auto_memory):
        repo_files = set(f for f in os.listdir(repo_memory) if f.endswith(".md"))
        auto_files = set(f for f in os.listdir(auto_memory) if f.endswith(".md"))
        drift = len(repo_files.symmetric_difference(auto_files))
        if drift <= 3:  # Allow small drift
            passes += 1
        else:
            issues.append(f"Memory: {drift} files differ between auto-memory and repo")
    else:
        issues.append("Memory: can't check split-brain (missing directory)")

    # --- Domain 7: Pipeline (2 checks) ---
    # V16: content-log.md exists
    content_log = os.path.join(project_dir, "docs", "_active", "content-log.md")
    if os.path.isfile(content_log):
        passes += 1
    else:
        issues.append("Pipeline: content-log.md missing")

    # V17: validate-content.py parses
    validate_path = os.path.join(hooks_dir, "validate-content.py")
    if os.path.isfile(validate_path):
        try:
            with open(validate_path, "r") as f:
                source = f.read()
            compile(source, validate_path, "exec")
            passes += 1
        except SyntaxError:
            issues.append("Pipeline: validate-content.py has syntax error")
    else:
        issues.append("Pipeline: validate-content.py missing")

    # --- Domain 8: Folders (2 checks) ---
    echo_in = os.path.join(project_dir, "echo", "intake")
    echo_out = os.path.expanduser("~/Documents/Echo-Exports")

    # V18: Echo IN accessible
    if os.path.isdir(echo_in):
        passes += 1
    else:
        issues.append("Folders: Echo IN directory missing")

    # V19: No credentials in working folders
    cred_extensions = (".env", ".csv", ".pem", ".key")
    cred_found = False
    for folder in [echo_in, echo_out]:
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if any(f.lower().endswith(ext) for ext in cred_extensions):
                    cred_found = True
                    break
    if not cred_found:
        passes += 1
    else:
        issues.append("Folders: credential files found in working folders")

    # V19b: Folder pile-up thresholds (informational flags, not counted vitals)
    # Surfaces "go run /tidy" prompt when Intake/Exports drift past comfort.
    INTAKE_THRESHOLD = 15
    EXPORT_THRESHOLD = 10
    try:
        if os.path.isdir(echo_in):
            intake_count = len([f for f in os.listdir(echo_in) if not f.startswith(".")])
            if intake_count > INTAKE_THRESHOLD:
                issues.append(
                    f"Folders: Echo-Intake has {intake_count} files "
                    f"(>{INTAKE_THRESHOLD}) — run `/tidy folders` before drift compounds"
                )
        if os.path.isdir(echo_out):
            export_count = len([f for f in os.listdir(echo_out) if not f.startswith(".")])
            if export_count > EXPORT_THRESHOLD:
                issues.append(
                    f"Folders: Echo-Exports has {export_count} files "
                    f"(>{EXPORT_THRESHOLD}) — `/closing` Phase 3 will archive"
                )
    except Exception:
        pass

    # --- Domain 9: Zones (3 checks) ---
    zone_guard = os.path.join(hooks_dir, "zone-guard.py")
    batch_counter = os.path.join(hooks_dir, "batch-counter.py")

    # V20: zone-guard.py has ZONE_SECURITY
    if os.path.isfile(zone_guard):
        try:
            with open(zone_guard, "r") as f:
                zg_content = f.read()
            if "ZONE_SECURITY" in zg_content or "Security" in zg_content:
                passes += 1
            else:
                issues.append("Zones: zone-guard.py missing ZONE_SECURITY")
        except Exception:
            issues.append("Zones: zone-guard.py unreadable")
    else:
        issues.append("Zones: zone-guard.py missing")

    # V21: batch-counter.py has deny
    if os.path.isfile(batch_counter):
        try:
            with open(batch_counter, "r") as f:
                bc_content = f.read()
            if "deny" in bc_content.lower() or "block" in bc_content.lower():
                passes += 1
            else:
                issues.append("Zones: batch-counter.py missing deny/block")
        except Exception:
            issues.append("Zones: batch-counter.py unreadable")
    else:
        issues.append("Zones: batch-counter.py missing")

    # V22: Echo OUT accessible
    if os.path.isdir(echo_out):
        passes += 1
    else:
        issues.append("Folders: Echo OUT directory missing")

    # V23: Protocol 4 Stop prompt-hook present (Mac App drift guard)
    # Catches silent removal of unverified-claim blocker.
    try:
        settings_path = os.path.join(project_dir, ".claude", "settings.json")
        with open(settings_path, "r") as f:
            settings = json.load(f)
        stop_hooks = settings.get("hooks", {}).get("Stop", [])
        has_prompt_hook = any(
            h.get("type") == "prompt"
            for entry in stop_hooks
            for h in entry.get("hooks", [])
        )
        if has_prompt_hook:
            passes += 1
        else:
            issues.append(
                "Enforcement: Protocol 4 Stop prompt-hook MISSING — "
                "unverified success claims will not be caught. "
                "Run `git diff .claude/settings.json` to see what was removed, "
                "then `git checkout .claude/settings.json` to restore."
            )
    except Exception:
        issues.append("Enforcement: cannot verify Stop prompt-hook (settings.json unreadable)")

    # Determine status
    if passes == total:
        status = "GREEN"
    elif passes >= total - 4:
        status = "YELLOW"
    else:
        status = "RED"

    # Build summary for YELLOW
    summary_parts = issues[:3]
    summary = "; ".join(summary_parts) if summary_parts else ""

    return status, {"pass": passes, "total": total, "issues": issues, "summary": summary}


def main():
    # Consume stdin (may be minimal JSON)
    try:
        sys.stdin.read()
    except Exception:
        pass

    # Reset batch-counter state — avoids carrying write-op count across
    # back-to-back sessions within the /tmp lifetime (sub-reboot window).
    # Each session starts with a fresh 25-op budget.
    try:
        batch_state = "/tmp/echo-batch-counter.json"
        if os.path.isfile(batch_state):
            os.remove(batch_state)
    except Exception:
        pass

    project_dir = get_project_dir()
    now = datetime.now()
    now_ts = now.timestamp()
    active_dir = os.path.join(project_dir, "docs", "_active")
    brief_names = ["cmo-brief.md", "cso-brief.md", "cto-brief.md", "gc-brief.md", "cfo-brief.md"]
    # Derive memory paths early (used by multiple sections)
    project_hash = project_dir.replace("/", "-")
    auto_memory = os.path.expanduser(f"~/.claude/projects/{project_hash}/memory")
    repo_memory = os.path.join(project_dir, "memory")
    memory_md = os.path.join(auto_memory, "MEMORY.md")
    if not os.path.isfile(memory_md):
        memory_md = os.path.join(repo_memory, "MEMORY.md")

    lines = []
    lines.append("=" * 60)
    lines.append("ECHO SESSION START")
    lines.append("=" * 60)

    # 1. Date + day of week
    lines.append(f"Date: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')})")

    # 2. Saturday → Sunday rule
    if now.strftime("%A") == "Saturday":
        lines.append("")
        lines.append(">>> SUNDAY RULE: Pre-schedule ALL Sunday content today.")

    # 3. Reminders — One-Time items
    reminders_path = os.path.join(project_dir, "memory", "reminders.md")
    flagged = parse_reminders(reminders_path)
    if flagged:
        lines.append("")
        lines.append("FLAGGED REMINDERS:")
        for item in flagged:
            lines.append(item)

    # 4. Active work — stuck items (from active-work.md; carryover-tracker.md DEPRECATED S224)
    active_work_path = os.path.join(project_dir, "memory", "active-work.md")
    stuck = parse_active_work_stuck(active_work_path)
    if stuck:
        lines.append("")
        lines.append("STUCK ACTIVE WORK (carried 3+ sessions):")
        for carried, item in stuck:
            lines.append(f"  [{carried}x] {item}")

    # 4a. NOW.md — Layer 0 HUD (Top of Mind)
    now_md_path = os.path.join(project_dir, "memory", "NOW.md")
    if os.path.isfile(now_md_path):
        try:
            with open(now_md_path, "r") as f:
                now_content = f.read()
            # Strip frontmatter
            if now_content.startswith("---"):
                end = now_content.find("---", 3)
                if end != -1:
                    now_content = now_content[end + 3:].strip()
            lines.append("")
            lines.append("=" * 40)
            lines.append("TOP OF MIND (NOW.md)")
            lines.append("=" * 40)
            lines.append(now_content)
        except (IOError, OSError):
            lines.append("")
            lines.append(">>> WARNING: memory/NOW.md unreadable")

    # 4b. Hot Rules injection (from rule-violations.json)
    violations_path = os.path.join(project_dir, "echo", "rule-violations.json")
    if os.path.isfile(violations_path):
        try:
            with open(violations_path, "r") as f:
                violations = json.load(f)
            # Sort by count descending, take top 5
            sorted_rules = sorted(violations.items(), key=lambda x: x[1].get("count", 0), reverse=True)[:5]
            if sorted_rules:
                lines.append("")
                lines.append("HOT RULES (most violated — from rule-violations.json):")
                for slug, data in sorted_rules:
                    count = data.get("count", 0)
                    severity = data.get("severity", "?").upper()
                    last = data.get("last_session", "?")
                    rule_text = data.get("rule", slug)
                    lines.append(f"  [{count}x {severity}] {rule_text} (last: {last})")
        except (IOError, json.JSONDecodeError):
            pass

    # 4b. Session continuity check
    import subprocess
    handoffs_dir = os.path.join(project_dir, "echo", "handoffs")
    if not os.path.exists(active_work_path):
        lines.append("")
        lines.append(">>> CRITICAL: active-work.md missing. Run /closing to rebuild.")
    if os.path.isdir(handoffs_dir):
        handoff_files = sorted(
            [f for f in os.listdir(handoffs_dir) if f.endswith(".md")],
            key=lambda f: os.path.getmtime(os.path.join(handoffs_dir, f)),
            reverse=True
        )
        if handoff_files:
            latest = os.path.join(handoffs_dir, handoff_files[0])
            mtime = os.path.getmtime(latest)
            age_hours = (now.timestamp() - mtime) / 3600
            if age_hours > 24:
                try:
                    git_result = subprocess.run(
                        ["git", "status", "--porcelain"],
                        capture_output=True, text=True, cwd=project_dir, timeout=5
                    )
                    uncommitted = [l for l in git_result.stdout.strip().split("\n") if l.strip()]
                    if uncommitted:
                        lines.append("")
                        lines.append(f">>> SESSION GAP: Last handoff was {int(age_hours)}h ago. {len(uncommitted)} uncommitted changes detected. Previous session may not have saved work.")
                except Exception:
                    pass

    # 4c. Handoff reading instruction (unconditional) — sort by mtime, not name (S195 bugfix)
    if os.path.isdir(handoffs_dir):
        handoff_files_sorted = sorted(
            [f for f in os.listdir(handoffs_dir) if f.endswith(".md")],
            key=lambda f: os.path.getmtime(os.path.join(handoffs_dir, f)),
            reverse=True
        )
        if handoff_files_sorted:
            latest_name = handoff_files_sorted[0]
            lines.append("")
            lines.append(f">>> READ HANDOFF: echo/handoffs/{latest_name} — BEFORE responding to user. This is the previous session's briefing.")

    # 4d. Session lifecycle state (S273 + S307 expansion)
    # Detects 3 marker types so /echo and /status can surface all closure states:
    #   ## CLOSING — RUN AT ...     → Echo end-of-day (locks /closing for the day)
    #   ## BARD-EOD — RUN AT ...    → Bard persona close (does NOT lock /closing)
    #   ## ORACLE-EOD — RUN AT ...  → Oracle persona close (does NOT lock /closing)
    # Plus persona session counts from memory/session-counters.json.
    today_str = now.strftime("%Y-%m-%d")
    handoffs_override = os.environ.get("ECHO_TEST_HANDOFFS_DIR")
    today_handoff = os.path.join(
        handoffs_override or handoffs_dir, f"{today_str}.md"
    )

    # Persona session counts (always reported, even on fresh day)
    counters_override = os.environ.get("ECHO_TEST_COUNTERS")
    counters_path = counters_override or os.path.join(
        project_dir, "memory", "session-counters.json"
    )
    persona_counts = {"echo": "?", "bard": "?", "oracle": "?"}
    try:
        with open(counters_path, "r", encoding="utf-8") as f:
            counters = json.load(f)
        for key in ("echo", "bard", "oracle"):
            if key in counters:
                persona_counts[key] = str(counters[key])
    except (IOError, OSError, ValueError, KeyError):
        pass

    lines.append("")
    lines.append(
        f">>> SESSIONS: Echo S{persona_counts['echo']} · "
        f"Bard B{persona_counts['bard']} · "
        f"Oracle O{persona_counts['oracle']}"
    )

    if os.path.isfile(today_handoff):
        try:
            with open(today_handoff, "r", encoding="utf-8") as f:
                handoff_content = f.read()
            echo_closing = None
            bard_eod = None
            oracle_eod = None
            for line in handoff_content.splitlines():
                if line.startswith("## CLOSING — RUN AT"):
                    echo_closing = line.strip()
                elif line.startswith("## BARD-EOD — RUN AT"):
                    bard_eod = line.strip()
                elif line.startswith("## ORACLE-EOD — RUN AT"):
                    oracle_eod = line.strip()
            save_state_count = sum(
                1 for line in handoff_content.splitlines()
                if line.startswith("## S") and "save-state" in line.lower()
            )

            if echo_closing:
                lines.append(f">>> DAY CLOSED: {echo_closing[3:]}")
                lines.append(">>> Use /save-state for any further work today. /closing has already run.")
            elif save_state_count > 0:
                lines.append(f">>> SESSION LIFECYCLE: {save_state_count} save-state checkpoint(s) today, /closing not yet run.")
                lines.append(">>> End-of-day signal → /closing. Mid-session rotation → /save-state.")
            else:
                lines.append(">>> SESSION LIFECYCLE: fresh day, no markers yet.")

            if bard_eod:
                lines.append(f">>> BARD-EOD: {bard_eod[3:]}")
            if oracle_eod:
                lines.append(f">>> ORACLE-EOD: {oracle_eod[3:]}")
        except (IOError, OSError):
            pass
    else:
        lines.append(">>> SESSION LIFECYCLE: fresh day, no handoff yet.")

    # 5. File intake scan — Echo IN, Desktop, Downloads
    drop_locations = [
        ("Echo IN", os.path.join(project_dir, "echo", "intake")),
        ("Desktop", os.path.expanduser("~/Desktop")),
        ("Downloads", os.path.expanduser("~/Downloads")),
    ]
    # Use second-latest handoff mtime as cutoff (previous session's end).
    # The latest handoff = current session's briefing, written AFTER files were dropped.
    cutoff = 0
    if os.path.isdir(handoffs_dir):
        hf = sorted(
            [f for f in os.listdir(handoffs_dir) if f.endswith(".md")],
            reverse=True,
        )
        if len(hf) >= 2:
            cutoff = os.path.getmtime(os.path.join(handoffs_dir, hf[1]))
        # No previous handoff → show everything from last 48h
        elif not hf:
            cutoff = now.timestamp() - 48 * 3600

    intake_lines = []
    total_new = 0
    for label, path in drop_locations:
        if not os.path.isdir(path):
            continue
        try:
            new_files = []
            for f in os.listdir(path):
                if f.startswith("."):
                    continue
                fp = os.path.join(path, f)
                if os.path.isfile(fp) and os.path.getmtime(fp) > cutoff:
                    new_files.append(f)
            if new_files:
                # Categorize
                videos = [f for f in new_files if f.lower().endswith((".mp4", ".mov", ".avi"))]
                images = [f for f in new_files if f.lower().endswith((".png", ".jpg", ".jpeg", ".heic", ".webp"))]
                other = [f for f in new_files if f not in videos and f not in images]
                parts = []
                if videos:
                    parts.append(f"{len(videos)} video{'s' if len(videos)>1 else ''}")
                if images:
                    parts.append(f"{len(images)} image{'s' if len(images)>1 else ''}")
                if other:
                    parts.append(f"{len(other)} other")
                summary = ", ".join(parts)
                intake_lines.append(f"  {label}: {len(new_files)} new ({summary})")
                total_new += len(new_files)
            else:
                intake_lines.append(f"  {label}: 0 new")
        except (PermissionError, OSError):
            intake_lines.append(f"  {label}: [scan error]")

    if total_new > 0:
        lines.append("")
        lines.append("FILE INTAKE (since last session):")
        for il in intake_lines:
            lines.append(il)
        lines.append(">>> READ + 7-ANGLE ANALYZE new files before other work.")

    # 5a. Intake manifest backlog check
    manifest_path = os.path.join(project_dir, "echo", "intake", ".processed")
    try:
        if os.path.isfile(manifest_path):
            import json as _json
            manifest_data = _json.load(open(manifest_path))
            processed_paths = {item.get("path", "") for item in manifest_data.get("items", [])}
        else:
            processed_paths = set()

        # Check all drop locations for files NOT in manifest
        backlog_count = 0
        backlog_sources = []
        for label, path in drop_locations:
            if not os.path.isdir(path):
                continue
            try:
                for f in os.listdir(path):
                    if f.startswith("."):
                        continue
                    fp = os.path.join(path, f)
                    if os.path.isfile(fp) and fp not in processed_paths:
                        backlog_count += 1
                        if label not in backlog_sources:
                            backlog_sources.append(label)
            except (PermissionError, OSError):
                pass

        if backlog_count > 0:
            lines.append("")
            lines.append(f"INTAKE BACKLOG: {backlog_count} files unprocessed across {', '.join(backlog_sources)}. Run /intake.")
    except (IOError, OSError, ValueError):
        pass  # Manifest unreadable or invalid JSON — skip silently

    # 5a2. Grimoire energy check (Nervous System Flow 2)
    grimoire_energy = os.path.expanduser("~/Documents/Grimoire/habits/energy-log.md")
    if os.path.isfile(grimoire_energy):
        try:
            with open(grimoire_energy, "r") as f:
                energy_lines = [l.strip() for l in f.readlines() if l.strip() and not l.startswith("#") and not l.startswith("---")]
            if energy_lines:
                last_entry = energy_lines[-1]
                low_signals = ["low", "1 |", "2 |", "| 1", "| 2"]
                if any(s in last_entry.lower() for s in low_signals):
                    lines.append("")
                    lines.append(f"ENERGY: LOW — repurpose mode recommended. Last entry: {last_entry}")
        except (IOError, OSError):
            pass

    # 5a3. Mac-reports scan (Nervous System Flow 4)
    mac_reports_dir = os.path.join(project_dir, "echo", "mac-reports")
    if os.path.isdir(mac_reports_dir):
        try:
            new_reports = []
            for f in os.listdir(mac_reports_dir):
                if not f.endswith(".md") or f.startswith("."):
                    continue
                fp = os.path.join(mac_reports_dir, f)
                if os.path.isfile(fp) and os.path.getmtime(fp) > cutoff:
                    new_reports.append(f)
            if new_reports:
                lines.append("")
                lines.append(f"MAC-REPORTS: {len(new_reports)} new from Limb since last session:")
                for r in sorted(new_reports)[:5]:
                    lines.append(f"  {r}")
                if len(new_reports) > 5:
                    lines.append(f"  ... and {len(new_reports) - 5} more")
                # Check FLAGS.md specifically for unresolved flags
                flags_path = os.path.join(mac_reports_dir, "FLAGS.md")
                if os.path.isfile(flags_path):
                    try:
                        with open(flags_path, "r") as ff:
                            flags_content = ff.read()
                        unresolved = flags_content.count("## FLAG") - flags_content.count("RESOLVED")
                        if unresolved > 0:
                            lines.append(f"  >>> {unresolved} UNRESOLVED FLAG(S) — review echo/mac-reports/FLAGS.md")
                    except (IOError, OSError):
                        pass
        except (PermissionError, OSError):
            pass

    # 5a4. Tool Intelligence reports scan
    if os.path.isdir(mac_reports_dir):
        try:
            tool_intel_reports = [
                f for f in os.listdir(mac_reports_dir)
                if f.endswith("-tool-intel.md") and os.path.isfile(os.path.join(mac_reports_dir, f))
            ]
            if tool_intel_reports:
                # Check for FLAGS in tool-intel reports
                tool_flags = []
                for tr in sorted(tool_intel_reports):
                    tr_path = os.path.join(mac_reports_dir, tr)
                    try:
                        with open(tr_path, "r") as tf:
                            content = tf.read()
                        if "### FLAGS" in content:
                            # Extract flag lines (lines starting with "- [")
                            for tl in content.split("\n"):
                                tl_stripped = tl.strip()
                                if tl_stripped.startswith("- ["):
                                    tool_flags.append(tl_stripped)
                    except (IOError, OSError):
                        pass
                if tool_flags:
                    lines.append("")
                    lines.append("TOOL INTEL:")
                    for flag in tool_flags[:5]:
                        lines.append(f"  {flag}")
                    if len(tool_flags) > 5:
                        lines.append(f"  ... and {len(tool_flags) - 5} more")
                # Check for unprocessed reports (no <!-- PROCESSED --> marker)
                unprocessed = []
                for tr in sorted(tool_intel_reports):
                    tr_path = os.path.join(mac_reports_dir, tr)
                    try:
                        with open(tr_path, "r") as tf:
                            content = tf.read()
                        if "<!-- PROCESSED" not in content:
                            unprocessed.append(tr)
                    except (IOError, OSError):
                        pass
                if unprocessed:
                    lines.append(f"  >>> {len(unprocessed)} UNPROCESSED report(s) — run /tool-intel --process")
        except (PermissionError, OSError):
            pass

    # 5b. Unread intake reports (from background watcher)
    try:
        import glob as _glob
        unread_reports = []
        intake_pads = [
            ("Echo", os.path.join(project_dir, "echo", "intake")),
            ("Oracle", os.path.join(project_dir, "oracle", "intake")),
            ("CFO", os.path.join(project_dir, "echo", "intake", "cfo")),
            ("Personal", os.path.join(project_dir, "echo", "intake", "personal")),
        ]
        for domain_label, pad_dir in intake_pads:
            if not os.path.isdir(pad_dir):
                continue
            for fname in os.listdir(pad_dir):
                if not fname.endswith(".md") or fname.startswith("."):
                    continue
                fpath = os.path.join(pad_dir, fname)
                try:
                    with open(fpath, "r") as rf:
                        head = rf.read(500)
                    if "read: false" in head:
                        unread_reports.append((domain_label, fname))
                except (IOError, OSError):
                    pass
        if unread_reports:
            lines.append("")
            lines.append(f"INTAKE REPORTS: {len(unread_reports)} unread from background watcher:")
            for domain_label, fname in unread_reports[:5]:
                lines.append(f"  [{domain_label}] {fname}")
            if len(unread_reports) > 5:
                lines.append(f"  ... and {len(unread_reports) - 5} more")
            lines.append(">>> Review with /intake or read reports directly.")
    except Exception:
        pass

    # 5c. Uncommitted plan files check
    plans_dir = os.path.join(project_dir, ".claude", "plans")
    if os.path.isdir(plans_dir):
        plan_files = [f for f in os.listdir(plans_dir) if f.endswith(".md")]
        if plan_files:
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain", ".claude/plans/"],
                    capture_output=True, text=True, cwd=project_dir, timeout=5
                )
                uncommitted = [l for l in result.stdout.strip().split("\n") if l.strip()]
                if uncommitted:
                    lines.append("")
                    lines.append("⚠️  UNCOMMITTED PLAN FILES (prior session may not have saved):")
                    for f in uncommitted:
                        lines.append(f"  {f.strip()}")
            except Exception:
                pass

    # 5d. Tier 1 Vitals (Diagnostic v4.0 — 23 checks across 9 domains + Mac App drift guard)
    vitals_status, vitals_details = run_vitals(project_dir)
    lines.append("")
    total_vitals = vitals_details.get('total', 23)
    if vitals_status == "GREEN":
        lines.append(f"SYSTEM HEALTH: GREEN ({vitals_details['pass']}/{total_vitals} vitals pass)")
    elif vitals_status == "YELLOW":
        lines.append(f"SYSTEM HEALTH: YELLOW ({vitals_details['pass']}/{total_vitals} — {vitals_details['summary']})")
    else:
        lines.append(f"SYSTEM HEALTH: RED ({vitals_details['pass']}/{total_vitals})")
        lines.append(">>> CRITICAL: Run /diagnostic deep before other work")
        if vitals_details.get("issues"):
            for issue in vitals_details["issues"][:5]:
                lines.append(f"  {issue}")

    # 5c. Hook permission audit (AI-MTG21-10 Option A — S363)
    hook_perm_warning = check_hook_perms(project_dir)
    if hook_perm_warning:
        lines.append("")
        lines.append(hook_perm_warning)

    # 5b2. Board brief staleness dashboard
    if os.path.isdir(active_dir):
        brief_status = []
        for bname in brief_names:
            bp = os.path.join(active_dir, bname)
            label = bname.replace("-brief.md", "").upper()
            if os.path.isfile(bp):
                age_days = int((now_ts - os.path.getmtime(bp)) / 86400)
                if age_days > 14:
                    brief_status.append(f"  {label}: {age_days}d — CRITICAL")
                elif age_days > 7:
                    brief_status.append(f"  {label}: {age_days}d — STALE")
            else:
                brief_status.append(f"  {label}: MISSING")
        stale_briefs_list = [b for b in brief_status if "STALE" in b or "CRITICAL" in b or "MISSING" in b]
        if stale_briefs_list:
            lines.append("")
            lines.append("BOARD BRIEFS:")
            for b in stale_briefs_list:
                lines.append(b)

    # 5b3. Content queue gap detection (next 3 days)
    queue_path = os.path.join(project_dir, "docs", "_active", "queue-status.md")
    if os.path.isfile(queue_path):
        try:
            with open(queue_path, "r") as f:
                queue_content = f.read()
            gap_count = queue_content.upper().count("**GAP**")
            if gap_count > 0:
                lines.append("")
                lines.append(f"CONTENT GAPS: {gap_count} gap slots detected in queue-status.md")
                lines.append("  >>> Run /plan-week or /post to fill gaps.")
        except (IOError, OSError):
            pass

    # 5b4. MCP health check (config-level)
    mcp_json_path = os.path.join(project_dir, ".mcp.json")
    if os.path.isfile(mcp_json_path):
        try:
            with open(mcp_json_path, "r") as f:
                mcp_data = json.load(f)
            server_count = len(mcp_data.get("mcpServers", {}))
            # Check for disabled servers
            disabled = [
                name for name, cfg in mcp_data.get("mcpServers", {}).items()
                if cfg.get("disabled", False)
            ]
            if disabled:
                lines.append("")
                lines.append(f"MCP: {server_count} servers configured, {len(disabled)} DISABLED ({', '.join(disabled[:3])})")
            # else: healthy — no output needed
        except (IOError, json.JSONDecodeError):
            lines.append("")
            lines.append("MCP: .mcp.json parse error — check config")

    # 5b5. Tool quota alerts (from MEMORY.md known issues)
    if os.path.isfile(memory_md):
        try:
            with open(memory_md, "r") as f:
                mem_text = f.read()
            quota_alerts = []
            if "over quota" in mem_text.lower() or "quota" in mem_text.lower():
                # Check for ElevenLabs quota
                if "elevenlabs" in mem_text.lower() and ("over quota" in mem_text.lower() or "resets" in mem_text.lower()):
                    quota_alerts.append("ElevenLabs: over quota (check reset date)")
            if "expired" in mem_text.lower() and "youtube" in mem_text.lower():
                quota_alerts.append("YouTube MCP: auth expired")
            if quota_alerts:
                lines.append("")
                lines.append("TOOL ALERTS:")
                for a in quota_alerts:
                    lines.append(f"  {a}")
        except (IOError, OSError):
            pass

    # 5b6. Scrape queue pending + extraction backlog
    scrape_queue_path = os.path.join(project_dir, "echo", "scrape-queue.md")
    if os.path.isfile(scrape_queue_path):
        try:
            with open(scrape_queue_path, "r") as f:
                sq_content = f.read()

            # Count pending sources (table rows under ## Pending)
            pending_sources = []
            in_pending = False
            for line in sq_content.splitlines():
                if line.strip().startswith("## Pending"):
                    in_pending = True
                    continue
                if in_pending and line.strip().startswith("## "):
                    break
                if in_pending and line.strip().startswith("|"):
                    # Skip header row and separator
                    stripped = line.strip()
                    if stripped.startswith("| #") or stripped.startswith("|---") or stripped.startswith("| ---"):
                        continue
                    # Extract richness stars
                    stars_match = re.search(r'[★☆]+', line)
                    if stars_match:
                        pending_sources.append(stars_match.group(0))
                    else:
                        pending_sources.append("?")

            # Count extraction backlog
            extraction_count = 0
            in_backlog = False
            for line in sq_content.splitlines():
                if line.strip().startswith("## Extraction Backlog"):
                    in_backlog = True
                    continue
                if in_backlog and line.strip().startswith("## "):
                    break
                if in_backlog:
                    # Look for patterns like "~96 extractions pending" or "10 manifest items pending"
                    ext_match = re.search(r'~?(\d+)\s+(?:extractions?|manifest items?)\s+pending', line)
                    if ext_match:
                        extraction_count += int(ext_match.group(1))

            if pending_sources or extraction_count > 0:
                parts = []
                if pending_sources:
                    stars_str = ", ".join(pending_sources)
                    parts.append(f"{len(pending_sources)} source(s) pending ({stars_str})")
                if extraction_count > 0:
                    parts.append(f"{extraction_count} extractions awaiting Librarian")
                lines.append("")
                lines.append(f"SCRAPE QUEUE: {' | '.join(parts)}")
                lines.append("  >>> Run /scrape to process queue.")
        except (IOError, OSError):
            pass

    # 5b7. Superpowers default reminder
    lines.append("")
    lines.append("SUPERPOWERS DEFAULT: Brainstorm → Plan → Execute. No exceptions.")
    lines.append("")
    lines.append("NEEDLE-MOVER FIRST: Eisenhower-sort all tasks. Q1/Q2 before Q3/Q4. Never lead with admin.")

    # 5c. AutoDream freshness check (threshold: 24h)
    dream_log_path = os.path.join(project_dir, "echo", "dream-log.md")
    if os.path.exists(dream_log_path):
        try:
            dream_mtime = os.path.getmtime(dream_log_path)
            hours_since_dream = (now_ts - dream_mtime) / 3600
            if hours_since_dream > 24:
                lines.append("")
                lines.append(f"DREAM STALE ({int(hours_since_dream)}h since last run). Will auto-run at /closing, or invoke /dream now.")
        except (IOError, OSError) as e:
            lines.append("")
            lines.append(f"WARNING: Could not read dream log: {e}")
    else:
        lines.append("")
        lines.append("DREAM STALE: AutoDream has never run. Run /dream or it runs at /closing.")

    # 5c2. Librarian (Warden) staleness check
    librarian_audit_path = os.path.join(project_dir, "echo", "librarian-last-audit.md")
    if os.path.isfile(librarian_audit_path):
        try:
            lib_mtime = os.path.getmtime(librarian_audit_path)
            hours_since_audit = (now_ts - lib_mtime) / 3600
            # Parse score from frontmatter
            with open(librarian_audit_path, "r") as f:
                lib_content = f.read()
            score_match = re.search(r"^score:\s*(\d+)", lib_content, re.MULTILINE)
            status_match = re.search(r"^status:\s*(\w+)", lib_content, re.MULTILINE)
            lib_score = score_match.group(1) if score_match else "?"
            lib_status = status_match.group(1) if status_match else "UNKNOWN"
            if hours_since_audit > 48:
                lines.append("")
                lines.append(f"LIBRARIAN STALE ({int(hours_since_audit)}h since last audit). Run /librarian audit.")
                lines.append(f"  Last health score: {lib_score}/100 ({lib_status})")
            else:
                # Still show score if it's RED or YELLOW
                if lib_status in ("RED", "YELLOW"):
                    lines.append("")
                    lines.append(f"LIBRARIAN: {lib_score}/100 ({lib_status}) — last audit {int(hours_since_audit)}h ago")
        except (IOError, OSError, UnicodeDecodeError):
            pass
    else:
        lines.append("")
        lines.append("LIBRARIAN STALE: No audit on record. Run /librarian audit.")

    # 5d. Transcript auto-create (not just a reminder — actually create the file)
    transcripts_dir = os.path.join(project_dir, "echo", "transcripts")
    if os.path.isdir(transcripts_dir):
        today_str = now.strftime("%Y-%m-%d")
        # Find highest session number from existing transcripts
        import re as _re
        max_session = 0
        for fname in os.listdir(transcripts_dir):
            m = _re.search(r'S(\d+)', fname)
            if m:
                max_session = max(max_session, int(m.group(1)))
        # Also check handoffs + MEMORY.md for higher session numbers
        for scan_dir in [
            os.path.join(project_dir, "echo", "handoffs"),
            os.path.join(project_dir, "memory"),
        ]:
            if not os.path.isdir(scan_dir):
                continue
            for fname in os.listdir(scan_dir):
                if not fname.endswith('.md'):
                    continue
                fpath = os.path.join(scan_dir, fname)
                try:
                    content = open(fpath).read(5000)  # first 5KB is enough
                    for m in _re.findall(r'S(\d+)', content):
                        max_session = max(max_session, int(m))
                except Exception:
                    pass
        # Check if a transcript was already created THIS calendar day
        # (each day gets one transcript — multiple sessions on same day share it)
        today_transcripts = sorted([
            f for f in os.listdir(transcripts_dir)
            if f.startswith(today_str) and f.endswith('.md')
        ])
        if today_transcripts:
            # Already have one for today — resume it
            latest = today_transcripts[-1]
            lines.append("")
            lines.append(f"TRANSCRIPT: echo/transcripts/{latest} — exists. Resume logging.")
        else:
            # Create new transcript for today
            next_session = max_session + 1
            transcript_name = f"{today_str}-S{next_session}.md"
            transcript_path = os.path.join(transcripts_dir, transcript_name)
            with open(transcript_path, 'w') as tf:
                tf.write(f"# Session Transcript — S{next_session} ({today_str})\n\n")
                tf.write("> Log decisions, blockers, pivots. One line per event.\n\n")
                tf.write(f"| Time | Type | Entry |\n")
                tf.write(f"|------|------|-------|\n")
            lines.append("")
            lines.append(f"TRANSCRIPT: echo/transcripts/{transcript_name} — CREATED. Log decisions, blockers, pivots.")

    # 5e. Memory integrity check
    # Derive auto-memory path dynamically from project_dir
    project_hash = project_dir.replace("/", "-")
    auto_memory = os.path.expanduser(
        f"~/.claude/projects/{project_hash}/memory"
    )
    repo_memory = os.path.join(project_dir, "memory")
    # Check auto-memory first, fall back to repo memory/MEMORY.md
    memory_md = os.path.join(auto_memory, "MEMORY.md")
    if not os.path.isfile(memory_md):
        memory_md = os.path.join(repo_memory, "MEMORY.md")

    integrity_warnings = []

    # Check for orphaned MEMORY.md references
    if os.path.exists(memory_md):
        try:
            with open(memory_md, "r") as f:
                md_content = f.read()
            # Extract markdown link targets like [text](filename.md)
            refs = re.findall(r"\]\(([^)]+\.md)\)", md_content)
            for ref in refs:
                # Skip non-file references (URLs, anchors)
                if ref.startswith("http") or ref.startswith("#"):
                    continue
                if not os.path.exists(os.path.join(auto_memory, ref)):
                    integrity_warnings.append(f"  ORPHAN: MEMORY.md links to {ref} but file missing")
        except (IOError, OSError, UnicodeDecodeError) as e:
            integrity_warnings.append(f"  WARNING: Could not read MEMORY.md: {e}")

    # Check for files in only one location (both directions)
    if os.path.isdir(repo_memory) and os.path.isdir(auto_memory):
        repo_files = set(f for f in os.listdir(repo_memory) if f.endswith(".md"))
        auto_files = set(f for f in os.listdir(auto_memory) if f.endswith(".md"))
        repo_only = repo_files - auto_files
        auto_only = auto_files - repo_files
        if repo_only:
            count = len(repo_only)
            shown = sorted(repo_only)[:5]
            for f in shown:
                integrity_warnings.append(f"  DRIFT: {f} in repo but missing from auto-memory")
            if count > 5:
                integrity_warnings.append(f"  ... and {count - 5} more repo-only files")
        if auto_only:
            count = len(auto_only)
            shown = sorted(auto_only)[:5]
            for f in shown:
                integrity_warnings.append(f"  UNCOMMITTED: {f} in auto-memory but not in repo")
            if count > 5:
                integrity_warnings.append(f"  ... and {count - 5} more uncommitted files")

    if integrity_warnings:
        lines.append("")
        lines.append("MEMORY INTEGRITY WARNINGS:")
        for w in integrity_warnings:
            lines.append(w)

    # --- Board Standup Auto-Generation ---
    standup_path = os.path.join(project_dir, "echo", "board", "standup.md")
    if os.path.isdir(os.path.join(project_dir, "echo", "board")):
        try:
            standup_lines = ["# Board Standup", "",
                             "> Auto-generated at session start from briefs + sentinel flags.",
                             "> Each board member gets one block: Status, Blocker, Need from board.",
                             "> Echo reads this first, routes cross-department requests.",
                             "> Regenerated each session — not append-only.", ""]

            members = ["CMO", "CSO", "CTO", "GC", "CFO"]
            flags_path = os.path.join(project_dir, "echo", "mac-reports", "FLAGS.md")
            flags_text = ""
            if os.path.isfile(flags_path):
                try:
                    with open(flags_path, "r") as f:
                        flags_text = f.read()
                except Exception:
                    pass

            action_items_path = os.path.join(project_dir, "echo", "board", "action-items.md")
            action_text = ""
            if os.path.isfile(action_items_path):
                try:
                    with open(action_items_path, "r") as f:
                        action_text = f.read()
                except Exception:
                    pass

            for member in members:
                brief_file = os.path.join(active_dir, f"{member.lower()}-brief.md")
                status_line = "—"
                blocker_line = "—"
                need_line = "—"

                if os.path.isfile(brief_file):
                    try:
                        with open(brief_file, "r") as f:
                            brief_content = f.read(4000)  # First 4KB
                        # Extract first real status line, skipping frontmatter,
                        # code blocks, tables, and metadata key:value lines
                        in_frontmatter = False
                        in_code_block = False
                        found = False
                        for i, line in enumerate(brief_content.split("\n")):
                            stripped = line.strip()
                            # Toggle frontmatter on --- boundary (first line only)
                            if stripped == "---":
                                if i == 0 or in_frontmatter:
                                    in_frontmatter = not in_frontmatter
                                continue
                            if in_frontmatter:
                                continue
                            if stripped.startswith("```"):
                                in_code_block = not in_code_block
                                continue
                            if in_code_block:
                                continue
                            if not stripped:
                                continue
                            if stripped.startswith("#") or stripped.startswith(">") or stripped.startswith("|"):
                                continue
                            # Skip YAML-like metadata (key: value) at top of content
                            import re as _re
                            if _re.match(r'^[A-Za-z][A-Za-z0-9_-]*:\s*\S', stripped):
                                continue
                            status_line = stripped[:120]
                            found = True
                            break
                        if not found:
                            status_line = "brief has no prose status line"
                    except Exception:
                        status_line = "brief unreadable"
                else:
                    status_line = "no brief found"

                # Check action items for blockers
                if action_text and member in action_text:
                    blocker_line = "has open action items"

                # Check FLAGS for cross-dept needs
                if flags_text and member.lower() in flags_text.lower():
                    need_line = "open flags — see FLAGS.md"

                standup_lines.append(f"## {member}")
                standup_lines.append(f"- **Status:** {status_line}")
                standup_lines.append(f"- **Blocker:** {blocker_line}")
                standup_lines.append(f"- **Need from board:** {need_line}")
                standup_lines.append("")

            with open(standup_path, "w") as f:
                f.write("\n".join(standup_lines))
            lines.append("")
            lines.append("BOARD STANDUP: Generated. See echo/board/standup.md")
        except Exception as e:
            lines.append(f"BOARD STANDUP: Generation failed — {e}")

    lines.append("")
    lines.append("=" * 60)

    print("\n".join(lines))
    sys.exit(0)


if __name__ == "__main__":
    main()
