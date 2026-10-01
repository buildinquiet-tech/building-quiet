# The Stack — Skills That Compose With These Hooks

> **The sentinels are the discipline layer.** This file is the **capability layer** — the community-sourced skills I actually run in production, curated by use case + paired with the sentinels that enforce their SOPs.

The hooks in this repo catch what shouldn't happen. The skills below are what makes Claude Code do real work — brainstorming before implementation, plans before code, verification before claims, debugging with method, parallel agents for breadth.

Every skill below is publicly installable from its original source. I'm not re-hosting them. The differentiator is the curation + the integration story — which skills survive contact with real production, which sentinels enforce them, and how they compose into a coherent workflow.

If you've ever shipped Claude Code into a real workflow and felt like "this is incredible but I keep losing control," this is the stack that fixes that.

---

## Why this exists

Claude Code's default behavior is competent-improviser mode: it'll do almost anything you ask, but without structure it'll skip planning, skip verification, drift mid-task, and confabulate state. Out of the box, this is fine for prototyping. For anything load-bearing, you want **discipline at the tool-call layer (the hooks in this repo) + structured workflows at the cognition layer (the skills below)**.

The hooks are mechanical: a fail-open Python script that fires before/after tool calls and refuses to let certain failure classes through. The skills are cognitive: invokable workflows that bias Claude toward better defaults — brainstorming first, planning before coding, TDD over write-test-after, verification before "done."

Without the skills, the hooks fire all the time. Without the hooks, the skills are aspirational ("I should brainstorm first") but get skipped under time pressure. The combination is the working substrate.

---

## The stack at a glance

```
┌─────────────────────────────────────────────────────────────────┐
│ COGNITION LAYER (skills — community-sourced)                    │
│                                                                 │
│ Discovery      → brainstorming, graphify                        │
│ Planning       → writing-plans, executing-plans                 │
│ Implementation → test-driven-development, subagent-driven-dev   │
│ Verification   → verification-before-completion, code-review    │
│ Debugging      → systematic-debugging                           │
│ Orchestration  → dispatching-parallel-agents, git-worktrees     │
│ Polish         → stop-slop, watch                               │
│ Sandboxing     → context-mode                                   │
└─────────────────────────────────────────────────────────────────┘
                              ⇅
┌─────────────────────────────────────────────────────────────────┐
│ DISCIPLINE LAYER (hooks — this repo, 46 sentinels)              │
│                                                                 │
│ PreToolUse   → routing-advisor · prompt-sentinel · zone-guard   │
│                · skill-pre-read · pbl-pre-read · cost-gate      │
│ PostToolUse  → batch-counter · api-verify · memory-sync         │
│ Stop         → claim-provenance · closing-integrity · bulk-burn │
│ SessionStart → session-start                                    │
└─────────────────────────────────────────────────────────────────┘
                              ⇅
┌─────────────────────────────────────────────────────────────────┐
│ CLAUDE CODE (the agent)                                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Top picks (operator-vetted, in production daily)

### Discovery layer

**`superpowers:brainstorming`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Walks through user intent, requirements, and design BEFORE you touch code or fire a long plan.
When I use it: Every non-trivial task. Pre-execution exploration that catches misunderstood requirements before they become wasted code.
Paired sentinel: `prompt-sentinel.py` enforces the structured prompt form on MCP dispatches AFTER brainstorming locks the intent.
Workflow: User asks for a feature → I invoke brainstorming → 3-5 question pass surfaces the real intent → THEN I move to planning. Skipping this step is the #1 cause of wasted implementation work I've measured.

**`graphify`** — Source: user-level skill (knowledge-graph extraction over any input).
What it does: Takes any input (code, docs, papers, images, videos) and produces a navigable knowledge graph that Claude can query.
When I use it: Any time I'm landing in an unfamiliar codebase or trying to understand cross-document relationships before making changes.
Paired sentinel: `skill-pre-read-sentinel.py` enforces that the graph gets opened before I make claims about the substrate.
Workflow: New codebase → graphify → ask the graph "what depends on X" → then plan changes with that map in mind. Saves the "I just modified a thing without realizing 12 other things called it" failure class.

### Planning layer

**`superpowers:writing-plans`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Produces a written multi-step plan with explicit dependencies, files to touch, and verification criteria.
When I use it: Before any multi-step implementation. Plans are durable across sessions; conversation memory isn't.
Paired sentinel: `plan-archiver.py` (PreToolUse on Write) archives the prior plan before a new one overwrites — no lost plan history.
Workflow: Complex feature request → brainstorm intent → write the plan → review the plan (sometimes with `requesting-code-review` skill) → only then move to execution.

**`superpowers:executing-plans`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Execute a written plan in a separate session with explicit review checkpoints between steps.
When I use it: Long multi-step plans where mid-execution drift is the real risk. The "review checkpoint" pattern is how I avoid 3-hour sessions that wander off-spec.
Paired sentinel: `bulk-burn-tracker.py` (Stop hook) classifies each turn's work type; routing-advisor catches when execution is spending hub time on what should have routed to a spoke.

### Implementation layer

**`superpowers:test-driven-development`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Rigid red-green-refactor TDD with no compromises.
When I use it: Any code change touching logic that can break. Skipped only for pure formatting/docs edits.
Paired sentinel: `verification-before-completion` (the skill) + the closing-integrity-sentinel (the hook) form a verification stack — TDD makes the verification possible, the sentinels enforce that it actually happened.
Workflow: Failing test FIRST → minimal code to pass → refactor → commit. The skill is rigid; the hooks back it up by refusing to let "done" claims survive without test evidence.

**`superpowers:subagent-driven-development`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Execute implementation plans by delegating independent tasks to subagents within the current session.
When I use it: Multi-file implementations where the steps don't share state. Subagent per file/module = parallelism + isolation.
Paired sentinel: `subagent-dispatch-sentinel.py` (Stop hook) refuses turn end if a state-changing subagent fired without a dispatch artifact written. Closes the "the subagent did something but I have no record" failure.

### Verification layer

**`superpowers:verification-before-completion`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Forces verification commands to run BEFORE any "done"/"fixed"/"passing" claim is made.
When I use it: Always. This is the single most-cited skill in my workflow.
Paired sentinel: `claim-provenance-sentinel.py` (Stop hook) detects state claims in prose without co-located source citation. Skill enforces the discipline; sentinel catches when it's slipped.
Workflow: Implementation done → run tests → read output → state results WITH evidence (not just "tests passed" — show the output). The "evidence before assertions" rule is what makes Claude Code trustworthy in production.

**`superpowers:requesting-code-review`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Self-requests a code review before merging.
When I use it: Before any merge to a main branch. Adversarial second-opinion catches what implementation-tunnel-vision missed.
Paired sentinel: `commit-nudge.py` (PostToolUse) prompts a commit at natural checkpoints, which is the right friction point to invoke the review skill.

**`superpowers:receiving-code-review`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Structured response to incoming code review — technical rigor over performative agreement.
When I use it: When reviews come in (human or AI). The skill's anti-glazing posture matters more than people think.
Paired sentinel: `capitulation-sentinel.py` (Stop hook) blocks "you're right" responses without audit evidence. Hard backstop against the agreeable-AI failure mode.

### Debugging layer

**`superpowers:systematic-debugging`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Structured bug investigation BEFORE proposing fixes — symptoms → hypotheses → bisection → root cause → fix.
When I use it: Any bug that isn't a one-line obvious fix. Skipping the structured pass is how I end up with fixes that pass the test but don't address the bug.
Paired sentinel: `propose-grep-gate.py` (PreToolUse) refuses propose-style writes when a grep-able source exists for the claim. Forces evidence-gathering before hypothesis-locking.

### Orchestration layer

**`superpowers:dispatching-parallel-agents`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Recognizes when 2+ independent tasks can be worked on without shared state and dispatches them in parallel.
When I use it: Any multi-piece task with non-overlapping concerns (multi-file edits, parallel research, independent verification chains).
Paired sentinel: `routing-advisor.py` (PreToolUse) hard-blocks when the wrong surface is being asked to do the work; `orch-advisor.py` (PreToolUse) advises when a task should have been routed to a spoke.

**`superpowers:using-git-worktrees`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Isolates feature work in a git worktree before touching the current workspace.
When I use it: Multi-agent execution patterns where agents need isolated working trees + before any work that might conflict with current uncommitted changes.
Paired sentinel: `zone-guard.py` (PreToolUse) enforces protected-path boundaries within whichever tree the agent is operating in.

**`superpowers:finishing-a-development-branch`** — Source: [obra/superpowers](https://github.com/obra/superpowers)
What it does: Structured completion check (merge / PR / cleanup decision) when implementation is done and tests pass.
When I use it: End of any feature branch. Prevents "done but not actually merged/PR'd/cleaned" drift.
Paired sentinel: `closing-integrity-sentinel.py` (Stop hook) re-derives numeric/state claims in the closing block from live probes. Closes the "I said the PR was created but the API call failed silently" failure class.

### Polish layer

**`stop-slop`** — Source: user-level skill.
What it does: Removes AI writing patterns from prose (em-dashes, "it's worth noting," "delve into," predictable rhythm).
When I use it: Any prose that ships externally (READMEs, blog posts, captions, docs).
Paired sentinel: `validate-content.py` (PostToolUse) brand-voice + banned-word check. stop-slop is the AI-tell removal; validate-content is the brand-fit check. Different layers, both run.

**`watch`** — Source: user-level skill (yt-dlp + ffmpeg + Whisper API pipeline).
What it does: Watch a video (URL or local), extract frames, pull transcript, answer questions about content.
When I use it: Any video reference (competitor analysis, tutorial review, demo extraction, content teardown).
Paired sentinel: `cost-gate.py` (PreToolUse) reminds about cost before Whisper API calls if the video has no captions.

### Sandboxing layer

**`context-mode`** — Source: plugin marketplace.
What it does: Run heavy file ops, log analysis, and large-output processing in a sandboxed Python subprocess that returns ONLY the derived answer — raw bytes stay out of conversation context.
When I use it: Any operation where the raw output would be >2KB. Saves context window budget aggressively.
Paired sentinel: `ctx-routing-guard.py` (PreToolUse) routes large read-only work to the sandboxed context tool instead of inline Bash. Skill provides the tool; sentinel enforces it gets used.

---

## How they compose — three real workflows

### Workflow 1: New feature implementation

```
brainstorming          → Lock the real requirement, surface hidden assumptions
writing-plans          → Multi-step plan with verification criteria per step
test-driven-development → Failing test before code
verification-before-completion → Test output WITH evidence before "done"
requesting-code-review → Adversarial second-opinion before merge
finishing-a-development-branch → Merge/PR/cleanup decision
```

Sentinels running silently across this whole flow: `prompt-sentinel`, `plan-archiver`, `zone-guard`, `claim-provenance`, `commit-nudge`, `closing-integrity`.

### Workflow 2: Investigation / debugging

```
graphify               → Map the substrate
systematic-debugging   → Symptoms → hypotheses → bisection → root cause
verification-before-completion → Confirm the fix actually addresses the cause
```

Sentinels running: `propose-grep-gate`, `skill-pre-read-sentinel`, `claim-provenance`.

### Workflow 3: Multi-agent parallel build

```
dispatching-parallel-agents → Identify the N independent tasks
using-git-worktrees        → Isolate each agent's work
subagent-driven-development → Delegate to subagents
verification-before-completion → Roll up evidence from each subagent
```

Sentinels running: `routing-advisor`, `orch-advisor`, `subagent-dispatch-sentinel`, `zone-guard`.

---

## Install notes

| Skill / Bundle | Install path | Notes |
|---|---|---|
| Superpowers bundle (brainstorming, plans, TDD, debugging, code review, orchestration, worktrees) | [github.com/obra/superpowers](https://github.com/obra/superpowers) | Single bundle, install once, all skills available. Strongly recommended. |
| graphify | User-level skill at `~/.claude/skills/graphify/` | Knowledge-graph engine over any input. |
| stop-slop | User-level skill at `~/.claude/skills/stop-slop/` | Anti-slop prose editor. |
| watch | User-level skill at `~/.claude/skills/watch/` | yt-dlp + ffmpeg + Whisper video watching. |
| context-mode | Claude Code plugin marketplace | Sandboxed file/output processing. |

All Anthropic-canonical CLI tooling (`/watch`, `/loop`, `/schedule`, `/verify`, `/code-review`, `/run`, etc.) ships with Claude Code itself — no install step.

---

## Attribution + license

Each skill is the work of its original author and ships under their license terms. I'm not redistributing the skill code in this repo — I'm curating the integration patterns. Follow the source links above to install from canonical sources.

This `SKILLS.md` curation is licensed MIT same as the rest of the repo. Lift it, adapt it, fork it.

---

## What's missing from this list

This is the stack as of v1 publish. Conspicuously absent (worth your time but not yet in the daily-use core):

- **Codex CLI** — cross-CLI parallel review via OpenAI's CLI. Useful for governance + LAW certification when you want a cross-family second opinion. Heavier setup; in operator's stack but not daily.
- **Higgsfield / kie.ai media-gen workflows** — production but not skill-installable; they're MCP integrations not skills.
- **Various plan-mode / specification skills** — present in superpowers but used less than the picks above.

If you're building a tighter or weirder stack and want to swap notes, the repo is open for issues and PRs.
