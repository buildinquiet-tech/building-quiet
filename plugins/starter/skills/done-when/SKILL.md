---
name: done-when
description: "Before starting a task, write 3 to 5 'done when' lines that a command or a file can prove. Before saying the task is done, check every line and report MET, NOT MET or UNPROVEN with the evidence. Use when the user says /starter:done-when, at the start of any task bigger than one edit, and before any 'done', 'fixed' or 'it works'. Do NOT use for a one-line question or a single obvious edit."
user-invocable: true
argument-hint: "[the task, in the user's words]"
---

# /starter:done-when: say what done means, then prove it

"Done" without a check is a feeling. This skill makes it a list you can run.

## Before the work

Write the task in the user's own words, then 3 to 5 lines under **Done when**. Each line must be
something you can prove later with a command, a file, or a visible result. Show them to the user
and start once they agree, or right away if they asked you to just go.

| Weak line | Checkable line |
|---|---|
| The login works | `npm test -- login` passes, and a wrong password shows "Invalid password" |
| The bug is fixed | The steps in the bug report no longer reproduce it, and a test covers it |
| It is faster | The page loads in under 1 s in `curl -w '%{time_total}'`, was 3.2 s |

Add one line under **Not in scope**: the nearby thing you will not touch.

## Before saying done

Go down the list. For each line, run the check and write one of:

- **MET**, with the evidence: the command and the line of output that proves it, or `file:line`.
- **NOT MET**, with what is missing.
- **UNPROVEN**, when you could not run the check, and why.

Then the verdict in one line: **Done** only when every line is MET. Otherwise say what is left.
Also list any file you changed that no line asked for.

## Rules

- Never mark a line MET from memory. Run the check now, after the last edit.
- A test you changed to make it pass does not prove anything. Say so if you did.
- If the plan changed mid-task, update the lines out loud before checking them.

Going further: this checks the lines you wrote. The [Proof Kit](https://buildinquiet.gumroad.com/l/proof-kit)
adds `diff-review`, which reads the whole diff against the spec and flags deleted or skipped tests
and swallowed errors.
