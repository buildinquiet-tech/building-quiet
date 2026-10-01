#!/usr/bin/env python3
"""scrape-activation-router.py — PostToolUse hook.

Closes the dewey→activation seam. After any Write/Edit/MultiEdit to a
scrape/research markdown file (or any Bash subprocess that may have
produced one), reads `routing_tag` from frontmatter and auto-routes:

  CONSUME → no-op (file-only, log only)
  ADAPT   → append placeholder row to drafts/intel-adapt-queue.md
  STEAL   → append placeholder row to drafts/intel-steal-queue.md
  WATCH   → set priority: high on matching surveillance-list.md row
            (matched by handle derived from frontmatter source URL)

Idempotent: tracks (path, tag) pairs in
.claude/hooks/scrape-activation-routed.json. Re-runs no-op if already
routed.

Spec: docs/superpowers/specs/2026-05-01-intel-activation-loop-v0.1.md §Layer 1
Closes carryover #133a "scrape→production bridge" (auto-routing tail).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parent.parent.parent)
GUARDED_DIRS = (REPO / "echo/scrape", REPO / "echo/research")
ADAPT_QUEUE = REPO / "drafts/intel-adapt-queue.md"
STEAL_QUEUE = REPO / "drafts/intel-steal-queue.md"
SURVEILLANCE = REPO / "echo/research/surveillance-list.md"
ROUTED_LEDGER = REPO / ".claude/hooks/scrape-activation-routed.json"
LOG = REPO / ".claude/hooks/scrape-activation-log.jsonl"

VALID_TAGS = {"CONSUME", "ADAPT", "STEAL", "WATCH"}
TAG_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
KV_RE = re.compile(r"^(\w[\w_]*)\s*:\s*(.+?)\s*$", re.MULTILINE)
HANDLE_RE = re.compile(r"@[\w\-.]+")

STEAL_QUEUE_TEMPLATE = """---
title: Intel Steal Queue — workflow/tool/skill clones from /scrape STEAL-tagged sources
description: Bottom-of-funnel sink for scrapes tagged STEAL. Feeds /tool-intel pipeline gates. Created by scrape-activation-router.py as Layer 1 of intel-activation-loop v0.1.
spec: docs/superpowers/specs/2026-05-01-intel-activation-loop-v0.1.md
created: {today}
managed_by: scrape-activation-router.py (auto-appends), /tool-intel (consumes), /closing Phase verifies non-stale
---

# Intel Steal Queue

> **Bottom-of-funnel sink** for scrapes tagged `STEAL`. Every entry = a workflow, tool, or skill worth replicating in our stack. /tool-intel reads this when looking for next adoption candidate.
>
> **Stale-check:** any entry sitting >30d without action triggers a `/closing` first-Sunday forced decision.

---

## Pending

| Date | Source file | What to steal | Target surface | Priority | Notes |
|------|-------------|---------------|:--------------:|:--------:|-------|
"""


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def is_guarded_md(path: Path) -> bool:
    if not path.suffix == ".md":
        return False
    try:
        path.resolve().relative_to(REPO)
    except ValueError:
        return False
    return any(str(path.resolve()).startswith(str(d.resolve())) for d in GUARDED_DIRS)


def read_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return {}
    m = TAG_RE.search(text)
    if not m:
        return {}
    body = m.group(1)
    fields = {}
    for km in KV_RE.finditer(body):
        k = km.group(1).lower()
        v = km.group(2).strip()
        v = re.sub(r"\s+#.*$", "", v).strip()
        v = v.strip('"').strip("'").strip()
        fields[k] = v
    return fields


def load_ledger() -> dict:
    if not ROUTED_LEDGER.exists():
        return {}
    try:
        return json.loads(ROUTED_LEDGER.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def save_ledger(ledger: dict) -> None:
    ROUTED_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    ROUTED_LEDGER.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")


def append_log(entry: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def derive_handle(fields: dict) -> str | None:
    for key in ("source", "title"):
        v = fields.get(key) or ""
        m = HANDLE_RE.search(v)
        if m:
            return m.group(0).rstrip(".,)")
    src = fields.get("source") or ""
    m = re.search(r"(?:youtube\.com|tiktok\.com|x\.com|twitter\.com|instagram\.com)/@?([\w\-.]+)", src)
    if m:
        return f"@{m.group(1)}"
    return None


def append_adapt_row(path: Path, fields: dict) -> bool:
    if not ADAPT_QUEUE.exists():
        return False
    rel = path.resolve().relative_to(REPO)
    title = fields.get("title", path.stem)
    row = (
        f"| {now_iso()} | [{path.stem}](../{rel}) | _needs-review_ | "
        f"_{title}_ — auto-routed ADAPT, awaiting curation | _TBD_ | NEW | "
        f"Auto-appended by scrape-activation-router. Promote/demote on review. |\n"
    )
    with ADAPT_QUEUE.open("a", encoding="utf-8") as f:
        f.write(row)
    return True


def append_steal_row(path: Path, fields: dict) -> bool:
    if not STEAL_QUEUE.exists():
        STEAL_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        STEAL_QUEUE.write_text(STEAL_QUEUE_TEMPLATE.format(today=now_iso()), encoding="utf-8")
    rel = path.resolve().relative_to(REPO)
    title = fields.get("title", path.stem)
    row = (
        f"| {now_iso()} | [{path.stem}](../{rel}) | "
        f"_{title}_ — auto-routed STEAL, awaiting curation | _TBD_ | NEW | "
        f"Auto-appended by scrape-activation-router. /tool-intel evaluates on next sweep. |\n"
    )
    with STEAL_QUEUE.open("a", encoding="utf-8") as f:
        f.write(row)
    return True


def bump_watch_priority(handle: str | None) -> bool:
    if not handle or not SURVEILLANCE.exists():
        return False
    text = SURVEILLANCE.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    changed = False
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        if handle not in line:
            continue
        parts = line.split("|")
        if len(parts) < 8:
            continue
        # priority is column 7 (index 6 in split, since leading | yields empty parts[0])
        prio_idx = 7
        if prio_idx >= len(parts):
            continue
        if "high" in parts[prio_idx].lower():
            continue
        parts[prio_idx] = parts[prio_idx].replace(parts[prio_idx].strip(), "high")
        # Pad to keep alignment
        target = parts[prio_idx].strip()
        parts[prio_idx] = f" {target:^8} "
        lines[i] = "|".join(parts)
        changed = True
    if changed:
        SURVEILLANCE.write_text("".join(lines), encoding="utf-8")
    return changed


def route_one(path: Path, ledger: dict) -> dict | None:
    if not is_guarded_md(path) or not path.exists():
        return None
    fields = read_frontmatter(path)
    tag = (fields.get("routing_tag") or "").upper()
    if tag not in VALID_TAGS:
        return None
    key = f"{path.resolve()}::{tag}"
    if key in ledger:
        return None
    result = {"path": str(path.resolve().relative_to(REPO)), "tag": tag, "ts": time.time(), "actions": []}
    if tag == "ADAPT":
        if append_adapt_row(path, fields):
            result["actions"].append("appended:intel-adapt-queue.md")
    elif tag == "STEAL":
        if append_steal_row(path, fields):
            result["actions"].append("appended:intel-steal-queue.md")
    elif tag == "WATCH":
        handle = derive_handle(fields)
        if bump_watch_priority(handle):
            result["actions"].append(f"bumped-priority:{handle}")
        else:
            result["actions"].append(f"watch-noop:{handle or 'no-handle'}")
    elif tag == "CONSUME":
        result["actions"].append("noop:consume")
    ledger[key] = {"ts": result["ts"], "actions": result["actions"]}
    append_log(result)
    return result


def scan_recent_writes(window_s: float = 90.0) -> list[Path]:
    cutoff = time.time() - window_s
    found: list[Path] = []
    for d in GUARDED_DIRS:
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            try:
                if p.stat().st_mtime >= cutoff:
                    found.append(p)
            except OSError:
                continue
    return found


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    candidates: list[Path] = []
    if tool in ("Edit", "Write", "MultiEdit"):
        fp = tool_input.get("file_path")
        if fp:
            candidates.append(Path(fp))
    elif tool == "Bash":
        cmd = (tool_input.get("command") or "").lower()
        # Only scan when Bash command plausibly wrote a scrape (perf guard)
        if any(token in cmd for token in ("dewey_dispatch", "echo/scrape", "echo/research", "scrape")):
            candidates.extend(scan_recent_writes())
    if not candidates:
        return 0
    ledger = load_ledger()
    routed_any = False
    for c in candidates:
        try:
            res = route_one(c, ledger)
            if res and res["actions"]:
                routed_any = True
        except Exception as exc:  # noqa: BLE001 — hook is best-effort, never block
            append_log({"path": str(c), "error": str(exc), "ts": time.time()})
    if routed_any:
        save_ledger(ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
