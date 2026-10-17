# Which one, when

Everything in the Starter, in plain English. The skills you call. The guards you never call: they run on their own.

## Skills (you type these)

| When you... | Type | What happens |
|---|---|---|
| Start a new project, or your CLAUDE.md is empty or stale | `/starter:claude-md` | Claude reads your repo, fills in the real commands, asks you 3 questions, shows the file before writing it |
| Hand Claude any task bigger than one edit | `/starter:done-when <the task>` | Claude writes 3 to 5 lines that define done. At the end it checks each one and shows the proof |
| Just installed the Starter, or a guard seems quiet | `/starter:check-setup` | A pass/fail list: Claude Code version, both guards' self-tests, one real block, your CLAUDE.md |

You can also just ask in plain words ("set up my CLAUDE.md", "is the starter working?"). Claude picks the skill.

## Guards (always on)

| Guard | Stops | Why it matters |
|---|---|---|
| `block-destructive` | `rm -rf` (any spelling, also after `&&` or `sudo`), `git push --force`, `git reset --hard` | These cannot be undone. `git push --force-with-lease` still works |
| `protect-secrets` | Claude reading or editing `.env` files, `.pem` and `.key` files, SSH keys, `~/.ssh`, `~/.aws` | What Claude reads goes into the chat. Keys stay out. `.env.example` is still allowed |

**Both fail closed.** A normal hook that crashes lets everything through. These are wired with
`onFailure: "block"` (Claude Code 2.1.295+), so if a guard ever breaks, the call is refused instead
of waved past. Docs: [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks).

**When a guard blocks something you meant to do:** run that one command yourself in a terminal.
The guards only stop Claude, never you.

## The template

`templates/CLAUDE.md` is what `/starter:claude-md` fills in. You can also copy it by hand.

## What the Starter does not do

- It does not check Claude's whole diff against your spec, or stop a scheduled routine from running on the wrong model.
- It does not keep state between sessions or carry a handoff note forward.

Those are in the [Proof Kit](https://buildinquiet.gumroad.com/l/proof-kit) ($39): 6 skills including `diff-review`, guards that fail closed where it matters, and a `claude plugin eval` report that shows each guard working.
