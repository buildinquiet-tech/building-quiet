---
name: check-setup
description: "Check that the Starter is installed and working: Claude Code is new enough for fail-closed guards, both guards pass their self-tests and really block, and the project has a CLAUDE.md. Reports a short pass/fail list with the fix for each fail. Use when the user says /starter:check-setup, right after installing the Starter, or when a guard seems not to fire. Do NOT use to audit prompts or skills in general (point the user to /doctor for that)."
user-invocable: true
---

# /starter:check-setup: prove the Starter works on this machine

Run each check, then report one line per check: **PASS** or **FAIL**, and for a FAIL, the fix.

## Checks

1. **Claude Code version.** Run `claude --version`. PASS at **2.1.295 or later**. Older versions
   ignore `onFailure: "block"`, so a broken guard would let everything through.
   Fix: `claude update`.
2. **Python.** Run `python3 --version`. PASS at 3.9 or later. Both guards are Python.
   Fix: install Python 3 from python.org or your package manager.
3. **Guard self-tests.** The hooks live at `<base directory>/../../hooks/`. Run:
   - `python3 <base directory>/../../hooks/block-destructive.py --selfcheck`
   - `python3 <base directory>/../../hooks/protect-secrets.py --selfcheck`
   PASS when each prints `selfcheck OK`. Fix: reinstall with
   `claude plugin install starter --marketplace buildinquiet-tech/building-quiet`.
4. **The guards fire for real.** Run `rm -rf ./starter-check-tmp` with the Bash tool.
   PASS when the call is refused with a message from `block-destructive.py`. Nothing is deleted
   either way: the folder does not exist. Fix: restart Claude Code so the plugin's hooks load.
5. **CLAUDE.md.** Look for `./CLAUDE.md` or `./.claude/CLAUDE.md`. PASS if one exists and has no
   `[bracket]` placeholders left. Fix: run `/starter:claude-md`.

## After the list

- One line on what to do first if anything failed.
- For a deeper look at prompts and skills, the built-in `/doctor` covers it. Do not repeat its work here.
