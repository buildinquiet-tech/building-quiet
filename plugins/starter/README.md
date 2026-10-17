# Starter

A free Claude Code plugin from Build in Quiet: a CLAUDE.md template, three setup skills, and two safety guards that block when they break.

## Install (one line)

```bash
claude plugin install starter --marketplace buildinquiet-tech/building-quiet
```

Then restart Claude Code and run `/starter:check-setup`. Needs Claude Code **2.1.295 or later** and Python 3.9+.

## What's inside

| | |
|---|---|
| `/starter:claude-md` | Writes your CLAUDE.md from the repo's real commands, shows it before saving |
| `/starter:done-when` | Defines done in checkable lines before a task, proves each one after |
| `/starter:check-setup` | Pass/fail list proving the Starter works on your machine |
| `block-destructive` guard | Refuses `rm -rf`, `git push --force`, `git reset --hard` |
| `protect-secrets` guard | Keeps `.env` files and keys out of the chat |
| `templates/CLAUDE.md` | The template the skill fills in |

Plain-English guide to all of it: [WHICH-ONE-WHEN.md](./WHICH-ONE-WHEN.md).

## Remove it

```bash
claude plugin uninstall starter
```

## Next step

The [Proof Kit](https://buildinquiet.gumroad.com/l/proof-kit) ($39) is the paid version: `diff-review` checks Claude's own diff against your spec before it says done, plus fail-closed guards for shared checkouts and scheduled routines, and an eval report that shows each guard working.

MIT licensed. Source: this folder.
