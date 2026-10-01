# auto-memory-doctrine

> Local-first, LAW-governed, Dewey-indexed auto-memory for Claude Code. Counter-positioning vs SaaS memory tools.

## What this is

The full memory architecture powering [@build.inquiet](https://instagram.com/build.inquiet)'s Claude Code session — published as an installable reference. NOT a SaaS. NOT a subscription. Local files, deterministic indexing, doctrine-enforced.

## What you get

| Component | What it does |
|---|---|
| `MEMORY.md` (template) | One-line-per-entry index — the only file auto-loaded into every session. Stays under 200 lines. |
| `memory/*.md` (templates) | Topic files — user / feedback / project / reference. Read on-demand, not auto-loaded. |
| `.claude/hooks/memory-sync.py` | Syncs auto-memory ↔ repo memory after every Edit/Write — no manual prompting. |
| `.claude/hooks/closing-integrity-sentinel.py` | Stop-hook that re-derives numeric claims in `/closing` from live grep/jq probes — catches narrative drift before it persists. |
| Doctrine doc | The full memory taxonomy + when-to-save rules + cross-link convention (`[[name]]`) |

## Why this exists (vs Supermemory et al)

Supermemory ($19/mo Pro) is a SaaS memory layer that auto-injects relevant memories at session start. It solves the "I keep re-explaining my business" problem with cross-project semantic search.

This skill solves the same problem with:
- **Local files** — every memory lives on your disk. No vendor lock-in. No API outage breaks your session. Free.
- **Dewey index** — `MEMORY.md` is a hand-curated catalog (one line per memory, ~150 char hook, file pointer). Claude reads it every session. Topic files load on-demand only.
- **Doctrine-governed** — feedback memos follow a strict format (rule + `Why:` + `How to apply:`) so future-Claude can judge edge cases instead of pattern-matching keywords.
- **LAW-bound** — corrections become hard-tier feedback memos that hooks enforce. Memory becomes runtime behavior, not passive recall.

If you want semantic search across years of projects, Supermemory is the right answer. If you want a memory layer that survives a SaaS shutdown, this is.

## Install

(coming — sourced from Echo's live `memory/` + `.claude/hooks/memory-sync.py` + relevant doctrine docs, redacted for operator anonymity. Issue tracker open for the first releasable cut.)

## License

MIT.
