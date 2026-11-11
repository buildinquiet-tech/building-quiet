# Run several Claude Code sessions at once without them colliding

People ask how to keep more than one session going: a shared task board, how to organize them in the desktop app, how to get past a single track. This is the short rule set from running several sessions on one repo every day. It is a working default, not a benchmark.

## The failure to avoid

Two sessions edit the same file, or one session commits while another has a half-finished change in the same folder. Nothing errors. You find out when a commit contains work you did not expect.

## Five rules

1. **One session, one folder.** Any session that edits code gets its own git worktree: `git worktree add ../myrepo-feature-x -b feature-x`. Claude Code can enter one for you (`claude --worktree`, see code.claude.com/docs/en/common-workflows). Sessions that only read can share a folder.
2. **One session, one job.** Write the job in one line before you start. If a session drifts into a second job, park that job as a note and start a new session for it.
3. **Commit only what is yours.** In a folder others may touch, name the files: `git commit --only -- path/one path/two`. Never `git add -A` there.
4. **Merge in the worktree, not in the shared folder.** In your worktree: `git fetch origin && git merge origin/main`, run the tests, then fast-forward the main folder with `git merge --ff-only your-branch`. If the fast-forward is refused, someone else moved main: go back to your worktree and merge again.
5. **Claim the job.** A plain file works as a shared task board. One file per job in `tasks/`, with the session's name on the first line when it takes the job. Before starting, list the folder and skip anything claimed.

## Starter task board (copy this)

```
tasks/2026-11-11-fix-login.md
claimed-by: session-login
status: in progress
done-when: the login test passes
```

## Check your own setup

Run `git worktree list` and `git status` in each folder. If two sessions show the same folder and both have changes, you have a collision waiting to happen.

If you want the guards that block a rule from slipping instead of warning about it, the Proof Kit does that: [buildinquiet.gumroad.com/l/proof-kit](https://buildinquiet.gumroad.com/l/proof-kit)
