# Claude Code Sentinels

> **46 production hooks for Claude Code.** Discipline at the tool-call layer — economic-burn prevention, routing audit, completion-depth enforcement, anonymity-class checks, claim-source provenance.

**→ See [SKILLS.md](./SKILLS.md) for the curated stack of community-sourced skills that compose with these hooks** — the cognition layer that pairs with the discipline layer here.

These are real, battle-tested hooks pulled from an active Claude Code working environment. Every hook is invoked daily; every one of them catches a specific class of failure that costs tokens, leaks state, or breaks a discipline rule.

This is the **substrate** layer. If you want a guided adoption (per-hook walkthrough, configuration kit, opinionated workflows), look at the Skill Pack project this repo seeds.

---

## Why this exists

Claude Code is incredibly powerful and incredibly easy to misuse. Without guardrails, an agent will:

- Burn the wrong model on bulk-mechanical work that should have routed to a spoke (~50% cost overrun).
- Make claims about state ("the build passed", "the queue is empty", "$X spent") without re-verifying.
- Edit files outside its declared scope.
- Repeat the same failure across sessions because no telemetry survives.
- Skip pre-read requirements for skills, plans, and doctrine files.
- Drift between what's claimed and what's actually on disk.

The 47 hooks in this repo close each of those gaps at the tool-call layer — fail-open, never bricking your work, but loudly telling you when discipline is slipping.

---

## Quickstart

```bash
# 1. Clone into your Claude Code working tree
git clone https://github.com/buildinquiet-tech/claude-code-sentinels.git

# 2. Copy the hooks (or symlink) into your .claude/hooks/ dir
cp claude-code-sentinels/hooks/*.py /path/to/your/project/.claude/hooks/

# 3. Wire them into .claude/settings.json (see "Wiring" below)

# 4. Set the project dir env var (if not auto-set by Claude Code)
export CLAUDE_PROJECT_DIR=/path/to/your/project
```

All hooks default to **advisory** behavior — they emit `[ADVISORY]` messages to stderr but exit 0, never blocking. Promote individual hooks to blocking via per-hook env vars (e.g., `ORCH_ADVISOR_TIER=blocking`).

---

## Hook reference (46 production hooks)

### PreToolUse hooks (fire BEFORE Bash / Edit / Write / Agent)

| File | One-line job |
|---|---|
| `zone-guard.py` | Refuses Edit/Write on protected paths (Security / Zone A / Zone B warn). |
| `bash-zone-guard.py` | Same enforcement for `mv`/`rm`/`cp` via Bash on protected paths. |
| `cost-gate.py` | Cost-estimate reminder before paid API curl calls. |
| `prompt-sentinel.py` | Enforces structured 3+5 prompt form on MCP / subagent dispatch. |
| `pbl-pre-read-sentinel.py` | Forces declared pre-read files to be opened before scoped task content writes. |
| `pbl-clone-default-sentinel.py` | Blocks content-artifact writes that lack `clone_source:` frontmatter. |
| `skill-pre-read-sentinel.py` | Generalizes the pre-read pattern across 5+ skill enforcement scopes. |
| `schedule-state-gate.py` | Refuses scheduler POSTs unless the corresponding state file has been advanced through the state-machine. |
| `deprecated-source-guard.py` | Refuses Reads of files graded DEPRECATED in `source-registry.yml`; names the canonical replacement. |
| `storyboard-entry-sentinel.py` | Blocks new storyboard Writes unless frontmatter has clone source + reference assets. |
| `routing-advisor.py` | Hard-blocks Edit/Write that classify as `drafting` over a size threshold without persist-provenance. |
| `cfo-subscription-delta-sentinel.py` | Refuses subscription-dashboard renders unless a same-day delta-scan artifact exists. |
| `persona-cross-push-sentinel.py` | Blocks unauthorized persona-EOD pushes that would overwrite primary handoffs. |
| `metricool-post-verify-pre.py` | Refuses scheduler POSTs to media-required platforms with empty `media` array. |
| `metricool-auth-enforcer.py` | Validates Metricool auth headers before request fires. |
| `ctx-routing-guard.py` | Routes large read-only work to sandboxed context tool instead of Agent. |
| `capability-guard.py` | Advisory capability-matrix enforcement on Agent dispatches. |
| `plan-mode-guard.py` | Plan-mode safety — prevents Writes during plan-only sessions. |
| `plan-archiver.py` | Archives existing plan file before overwrite. |
| `orch-advisor.py` | Soft-advisory before Bash/Edit/Write when prompt classifies as economic work-type; tier-controlled via env. |
| `propose-grep-gate.py` | Refuses propose-style writes when a grep-able source exists for the claim. |
| `scrape-activation-router.py` | Routes scrape-class work to the proper extraction surface. |
| `scrape-routing-tag-gate.py` | Validates scrape routing tags before tool call fires. |
| `law-enforcement-manifest-gate.py` | Refuses LAW-enforcement edits without the canonical manifest reference. |

### PostToolUse hooks (fire AFTER tool result)

| File | One-line job |
|---|---|
| `board-sentinel.py` | Domain monitor — injects domain-specific review when board-tracked surfaces are touched. |
| `batch-counter.py` | Enforces N write-ops per batch cap (warns at 0.8N, blocks at N). |
| `api-verify-reminder.py` | Injects verify-API-result reminder after external-API Bash calls. |
| `echo-out-guard.py` | Routes media artifacts to designated export dir, not the working repo. |
| `transcript-enforcer.py` | Enforces session transcript logging hygiene. |
| `memory-sync.py` | Syncs auto-memory ↔ repo memory after relevant Edits/Writes. |
| `commit-nudge.py` | Suggests `git commit` at natural checkpoints (N+ staged files). |
| `stale-citation-sentinel.py` | Flags claims that cite a file whose mtime is stale relative to a freshness threshold. |
| `context-pressure.py` | Monitors token-window usage; emits a save-state nudge before compression. |
| `tool-routing-advisor.py` | Logs spoke-used vs spoke-prescribed; tracks routing-doctrine adherence. |
| `sentinel-hall-monitor.py` | Monitors override patterns across all sentinels; flags repeat-override loops. |
| `config-journal-logger.py` | Logs all settings.json / hook config changes to a journal. |
| `validate-content.py` | Brand-voice + banned-word + FTC-compliance check on outbound content. |

### Stop hooks (fire at turn end)

| File | One-line job |
|---|---|
| `closing-integrity-sentinel.py` | Re-derives numeric/state claims in the final closing block from live probes; writes defects to a per-day sentinel file. |
| `claim-provenance-sentinel.py` | Detects operational state claims in prose without co-located source citation. |
| `schedule-queue-sync-sentinel.py` | Refuses turn end if scheduling activity occurred but queue-status was not updated. |
| `render-queue-sentinel.py` | Refuses turn end if render activity occurred but render-queue was not updated. |
| `subagent-dispatch-sentinel.py` | Refuses turn end if a state-changing subagent was invoked but no dispatch file was Written. |
| `feedback-memo-binding-sentinel.py` | Refuses turn end if a hard-tier feedback memo was Written without a paired enforcement-hook Write. |
| `bulk-burn-tracker.py` | Stop-hook telemetry — classifies prompt + logs economic-burn flags for routing-advisor calibration. |
| `time-estimate-sentinel.py` | Refuses turn end if a single-number time quote appeared without a bounded range + rationale. |
| `capitulation-sentinel.py` | Blocks "you're right" capitulation without audit evidence — enforces sparring-over-agreement. |

### SessionStart hooks (fire on session open)

| File | One-line job |
|---|---|
| `session-start.py` | Date stamp + flagged reminders + carryover items + intake scan. The whole-day briefing. |

---

## Adapt for your project

Three places to customize:

### 1. Project root

Most hooks resolve files relative to `$CLAUDE_PROJECT_DIR` (auto-set by Claude Code) or `os.getcwd()` as fallback. Both should already work without config.

### 2. Brand placeholders

Three hooks ship with placeholder brand handles you'll want to replace with your own:

- `validate-content.py` — line refs to `@yourbrand1`
- `prompt-sentinel.py` — `BRAND_RE` regex at top of file
- `pbl-pre-read-sentinel.py` — `BRAND_VOICE_MAP` dict + brand routing dict

`grep -lE '@?yourbrand[123]' hooks/*.py` finds every occurrence. Replace with your own brand handles.

### 3. Working-dir conventions

Several hooks write state to `echo/state/`, `echo/sentinel/`, `~/.echo/state/` paths. These are working-dir conventions, not brand identity. You can either:

- Adopt the convention (create those dirs in your project)
- Search-and-replace `echo/` with your own working-dir name (e.g., `state/`, `audit/`)

### 4. Settings wiring

Each hook attaches to a Claude Code hook surface (PreToolUse / PostToolUse / Stop / SessionStart). Sample `.claude/settings.json` snippet:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|Write",
        "hooks": [
          {"type": "command", "command": "python3 .claude/hooks/orch-advisor.py"},
          {"type": "command", "command": "python3 .claude/hooks/zone-guard.py"},
          {"type": "command", "command": "python3 .claude/hooks/cost-gate.py"}
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash|Edit|Write",
        "hooks": [
          {"type": "command", "command": "python3 .claude/hooks/batch-counter.py"},
          {"type": "command", "command": "python3 .claude/hooks/api-verify-reminder.py"}
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {"type": "command", "command": "python3 .claude/hooks/closing-integrity-sentinel.py"},
          {"type": "command", "command": "python3 .claude/hooks/claim-provenance-sentinel.py"}
        ]
      }
    ]
  }
}
```

See Claude Code's [hooks documentation](https://docs.claude.com/en/docs/claude-code/hooks) for the full wiring schema.

### 5. Tier control

Hooks default to **advisory** (stderr message, exit 0, never blocks). Promote individual hooks to **blocking** via per-hook env vars:

```bash
export ORCH_ADVISOR_TIER=blocking
export CLAIM_PROVENANCE_TIER=blocking
```

When blocking is enabled, triggering the hook returns exit code 2 (Claude Code's "block tool call" signal). Always run a soak window (7-14 days) in advisory mode before flipping to blocking.

---

## Tests

The `tests/` directory has 13 test files for the more complex hooks. Run with:

```bash
cd tests/
python3 -m pytest -v
```

Tests cover the gate logic (block vs pass), bypass paths, fail-open behavior, and fixture-based regression checks.

---

## Common patterns

If you're reading hook source as a learning exercise, here are the patterns that appear most:

- **Read stdin JSON payload** → parse → decide → exit 0 (proceed) or exit 2 (block).
- **Fail-open on any exception** — `try: main() except: sys.exit(0)`. Governance hooks must never brick a tool call on a bug.
- **Tier control** — `os.environ.get("HOOK_TIER", DEFAULT_TIER)`. Ships advisory; operator promotes to blocking after soak.
- **Bypass via env** — `os.environ.get("SENTINEL_OVERRIDE", "").strip() == "<reason>"` mutes the fire. Bypasses log to a separate file for weekly audit.
- **Provenance check** — before flagging a claim, look for a source citation within a ±N-char window in the same payload.
- **Cross-hook coordination** — `routing-advisor.py` (hard-block, narrow paths) and `orch-advisor.py` (soft-advise, broad surface) coordinate via a "would-block" check so neither double-advises.

---

## License

MIT — see [LICENSE](./LICENSE).

Adapt freely. Replace what doesn't fit. The discipline pattern travels even when the specific rules don't.
