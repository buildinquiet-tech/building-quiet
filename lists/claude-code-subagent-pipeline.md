# Claude Code subagents: one page per job

Four example pages for a content pipeline. Each is a Markdown file with YAML frontmatter, saved in `.claude/agents/` in your project. Copy them and change the descriptions to match your own work.

From the Claude Code docs (code.claude.com/docs/en/sub-agents):
- Subagents are Markdown files with YAML frontmatter. Only `name` and `description` are required; the body is the subagent's system prompt.
- Project subagents live in `.claude/agents/`.
- Claude uses each subagent's `description` to decide when to delegate.
- `model` accepts `sonnet`, `opus`, `haiku`, `fable`, a full model ID, or `inherit`.
- Each subagent runs in its own context window.
- Claude Code watches `.claude/agents/`, so an edited page is picked up by the next delegation with no restart.

## hook-finder.md
```markdown
---
name: hook-finder
description: Finds the first line of a post. Use when I give you a topic and need five opening lines.
model: sonnet
---
Give me five opening lines for the topic. Each one names a stake in the first sentence. No explainer openers. Return them as a numbered list.
```

## script-writer.md
```markdown
---
name: script-writer
description: Writes a short script from a chosen hook. Use after a hook is picked.
model: sonnet
---
Write a 30-second script from the chosen hook. One idea per line. End with one clear next step for the viewer.
```

## reviewer.md
```markdown
---
name: reviewer
description: Reviews a script. Use after the script is written and before anything is cut.
model: opus
---
Read the script alone. List anything unclear, anything unproven, and anything that repeats. Do not rewrite it. Return the list.
```

## cutter.md
```markdown
---
name: cutter
description: Cuts a script to length. Use after the reviewer's list is applied.
model: haiku
---
Cut the script to the target length without changing the order. Keep the hook and the last line.
```

## Run them in sequence
Ask Claude: "Use the hook-finder, then the script-writer, then the reviewer, then the cutter on this topic." Each returns its result and Claude passes the context to the next.

## When a step goes wrong
Edit the page, not the chat. The next delegation uses the updated page.

These pages illustrate the pattern; they are examples, not a product.
