# One question before every change
Ask Claude Code this before it makes a change:

> How do we make this change fully verifiable?

Credit: Matt Pocock's "prompt of the day" on X, 2026-11-05: https://x.com/mattpocockuk/status/2118313863917564136

What it does: Claude plans how to prove the change works before writing it, for example a failing test first, edge cases, running the full suite and quoting the pass output. Run it in plan mode first if you want the plan before any file changes.
