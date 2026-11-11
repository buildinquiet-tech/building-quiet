# Which model for which job

People keep asking when to use Opus, when Sonnet or Haiku is enough, and whether a second tool like Codex earns its place. This is the rule set used to run a real multi-project setup. It is a working default, not a benchmark. Measure it on your own work before you trust it.

## The three-row table

| Work | Default |
|---|---|
| Judgment: planning, review, anything with taste, anything you will publish | Your strongest model (Opus) |
| Mechanical work with a clear spec and a test that decides pass or fail | Sonnet, or Haiku if the test is strict |
| Bulk reading, extraction, summaries, first drafts | The cheapest option that can do it, including a second tool |

## Three rules that make it hold

1. **Pick the model before you start, not after it gets expensive.** Say it in one line: "Sonnet subagent, mechanical, test decides."
2. **Let a test decide, not your feelings.** If a cheaper model passes the check, it keeps the job. If it fails twice, the job moves up one model and you note why.
3. **The main chat is the judge, the subagent is the worker.** Put the model in the subagent's page so it applies every time:

```markdown
---
name: file-reader
description: Reads a folder and returns a short summary. Use for bulk reading before a decision.
model: haiku
---
Read the files you are given. Return at most 200 words: what is in them, and anything that looks wrong. Do not edit anything.
```

`model` accepts `sonnet`, `opus`, `haiku`, `fable`, a full model ID, or `inherit` (docs: code.claude.com/docs/en/sub-agents).

## When a second tool earns its place

- Use it for bulk builds from a clear spec, where you review the diff afterwards.
- Do not use it for reading or auditing. A tool that cannot see your repo's context will invent details; check anything it reports before you act on it.
- Whatever it returns, you review. The second tool saves typing, not judgment.

## Check it on your own work

Keep a small log of: job, model, passed first time (yes or no), minutes. After ten jobs the table above is replaced by your own numbers. That log is the part worth keeping.

If you want the guards that block a bad rule instead of warning about it, the Proof Kit does that: [buildinquiet.gumroad.com/l/proof-kit](https://buildinquiet.gumroad.com/l/proof-kit)
