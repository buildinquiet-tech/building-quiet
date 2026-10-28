# 5 free Claude Code plugins to install first (4 by Anthropic)

The list from the @build.inquiet Short of 2026-10-31. Every plugin below was read live on
**2026-10-27**: the repo is public, not archived, and carries an open license (Apache-2.0 or MIT).

> Make sure you trust a plugin before installing it. Nothing here was installed or security-reviewed
> for this list; it was checked for existence, license and what its own README says it does.

**Before you install any of them:** `claude plugin details <name>` shows its components and projected
token cost.

## 1. claude-code-setup (Anthropic)

Scans your codebase and recommends the top automations: hooks, skills, MCP servers, subagents and
slash commands. Read-only: it changes no files.

```
/plugin install claude-code-setup@claude-plugins-official
```

## 2. claude-md-management (Anthropic)

Audits your CLAUDE.md files against the current codebase. `/revise-claude-md` captures what a session
learned.

```
/plugin install claude-md-management@claude-plugins-official
```

## 3. security-guidance (Anthropic)

Pattern warnings on edits for about 25 risky patterns, an LLM review of the diff when Claude finishes a
turn, and a reviewer on `git commit`. Its reviews use your own Claude Code sign-in or API key.

```
/plugin install security-guidance@claude-plugins-official
```

## 4. code-review (Anthropic)

`/code-review` runs parallel agents on a pull request, scores each issue, and posts only issues at 80+
confidence. Needs the GitHub CLI (`gh`) installed and signed in.

```
/plugin install code-review@claude-plugins-official
```

## 5. agent-skills (Addy Osmani, MIT): the one that isn't Anthropic's

25 engineering skills across define, plan, build, verify, review and ship.

```
/plugin marketplace add addyosmani/agent-skills
/plugin install agent-skills@addy-agent-skills
```

## Sources

- Plugins 1-4: [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official),
  the marketplace Claude Code ships enabled by default.
- Plugin 5: [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills).
