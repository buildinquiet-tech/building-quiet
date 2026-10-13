# Fail-closed hook

A PreToolUse hook that refuses `rm -rf`, `git push --force` and `git reset --hard`, and **blocks the call if the hook itself breaks**.

## Why

By default a broken hook lets the action through. From the Claude Code docs:

> "On most events, when a hook fails or times out, Claude Code still carries out the action, so a policy hook with a wrong path or a crashing script lets everything through."

So a guard with a typo in its path is not a guard. One field changes that:

```json
"onFailure": "block"
```

The default is `"continue"`. Requires Claude Code **v2.1.295** or later.

## Files

| File | What |
|---|---|
| `.claude/settings.json` | Wires the hook to every Bash call, with `"onFailure": "block"` and a 10 second timeout |
| `.claude/hooks/check-command.py` | The guard. Exit 2 blocks and tells Claude why. Exit 0 lets the call through |

## Three things the docs say that most hooks get wrong

1. **Exit 1 does not block.** "Claude Code treats exit code 1 as a non-blocking error." A policy hook uses `exit 2`.
2. **A timeout does not block** unless `onFailure` is `"block"`.
3. **`onFailure` has no effect** on `Stop`, `SubagentStop`, `TaskCompleted`, `TeammateIdle` and background (`async`) hooks.

## Try it

```bash
cd demos/fail-closed-hook
claude --version        # 2.1.295 or later
claude                  # ask it to run: rm -rf build   -> blocked by check-command.py
```

Then break it on purpose. Make the guard crash: add `raise RuntimeError("broken")` as its first line after the imports. Python exits 1 on a crash, and exit 1 is not a block. With `"onFailure": "block"`, per the docs, every Bash call is refused with `... failed; blocking because onFailure is "block"`. Delete that line from settings.json and the same crashing hook lets everything through. Undo the `raise` when you are done.

A note on renaming the file as a break test: it does not prove anything for this hook. `python3` exits **2** when the script is missing, and 2 is the blocking code, so a renamed Python guard blocks with or without `onFailure`. A missing `node` or shell script exits with a different code and is let through. Test with a crash or a hang (a `time.sleep(30)` passes the 10 second `timeout`), not a rename.

Source: [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails), read 2026-10-12.
