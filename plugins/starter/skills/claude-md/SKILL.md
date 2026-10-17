---
name: claude-md
description: "Write or refresh this project's CLAUDE.md from the Starter template. Reads the repo for the facts it can find (stack, install, run and test commands), asks the user only for what it cannot read, and never overwrites an existing CLAUDE.md without showing the change first. Use when the user says /starter:claude-md, asks to set up or fix their CLAUDE.md, or opens a project that has none. Do NOT use to edit a CLAUDE.md the user did not ask to change."
user-invocable: true
---

# /starter:claude-md: a CLAUDE.md with real facts in it

A CLAUDE.md full of placeholders or wrong commands is worse than none: Claude trusts it.
Fill it from what the repo proves, ask for the rest, keep it short.

## Steps

1. **Look for an existing file.** Check `./CLAUDE.md` and `./.claude/CLAUDE.md`.
   - If one exists, this is a refresh: read it in full, keep everything the user wrote, and
     only add missing sections or fix lines that are now wrong. Go to step 5 when done.
2. **Read the template** at `<base directory>/../../templates/CLAUDE.md`.
3. **Read the repo for facts.** Only files that exist: `package.json` (scripts), `pyproject.toml`,
   `requirements.txt`, `Makefile`, `Cargo.toml`, `go.mod`, `docker-compose.yml`, `README.md`.
   Fill **Stack** and **How to run it** from these. Use the exact command strings you found.
   If a command is not written down anywhere, leave its bracket and say so. Never guess one.
4. **Ask the user, in one message, only what the repo cannot tell you:**
   - What the project is and who it is for (one line).
   - Anything Claude must never touch without asking.
   - How they like answers (short, detailed, code first).
5. **Show before you write.** Print the full new file (or, for a refresh, only the changed
   lines with a line before and after each). Write it after the user says yes.
6. **Check what you wrote:**
   - Every file path named in it exists (`ls` each one).
   - Every command under "How to run it" either ran, or is marked as not run yet.
   - Under 80 lines. If it is longer, cut, starting with anything Claude could read from the repo itself.

## Rules

- Delete the template's `<!-- -->` comments and any section left empty. No placeholder ships.
- Do not copy the README into CLAUDE.md. Link to it in one line instead.
- No secrets, tokens, or personal details in the file. It is usually committed.
