# The Queue Kit: Claude Code + Obsidian Content Queue Starter

The system from the video: Claude Code plans, drafts, and checks your content queue inside an Obsidian vault while you do something else. This kit is the exact starting structure. Set it up once, run it weekly.

## 1. The vault structure

Create these folders in any Obsidian vault (or plain folder, it's all markdown):

```
content/
  QUEUE.md          <- the one file that runs everything
  drafts/           <- Claude writes here, you approve here
  shipped/          <- approved posts move here after publishing
  voice.md          <- your brand voice rules (see section 3)
```

## 2. QUEUE.md: the file that runs the system

```markdown
# Content Queue

## Rules
- Batch weekly. Plan Monday, draft the whole week, never build daily.
- Nothing publishes without my approval. Claude drafts, I ship.

## This week
| Day | Platform | Topic | Status |
|-----|----------|-------|--------|
| Mon | TikTok   | <topic> | idea / drafted / approved / shipped |

## Idea bank
- (add ideas here all week; Monday's plan pulls from this list)
```

## 3. The CLAUDE.md starter

Drop this in the vault root. It turns Claude Code into your queue operator:

```markdown
# CLAUDE.md: Content Queue Operator

You manage my content queue in content/QUEUE.md.

## The loop
1. Read QUEUE.md. Find rows with status "idea".
2. Draft each one into content/drafts/<date>-<platform>-<slug>.md
   following voice.md exactly.
3. Set the row to "drafted". Never touch "approved" or "shipped" rows.
4. Show me every draft. I approve or kill. You never publish anything.

## Hard rules
- Read voice.md before every draft. No exceptions.
- One draft per file. Hook first line, CTA last line.
- If a topic is weak, say so and propose a stronger angle.
- Never invent metrics, results, or claims I didn't give you.
```

## 4. voice.md: make it yours

Three sections minimum: words you never use, three posts that sound like you (paste them), your one CTA. Claude follows what's written, not what you meant. Write it down.

## 5. The weekly run

Open the vault in Claude Code and say:

> "Run the queue. Plan the week from the idea bank, then draft everything."

Review the drafts in one sitting. Approve, kill, or edit. Move approved posts to your scheduler. That's the whole system: one planning session, one review session, per week.

---

# The bonuses

You came for the vault structure. Here's the part I usually keep for the paid stuff.

## Bonus 1: Week one, pre-filled

Ten idea templates for your idea bank. Fill the blank with your tool, your number, your result. Each one follows the hook rule that took me 35 dead posts to learn: name a specific tool, number, or result in line one.

1. "The 3 tools I stopped paying for since I built ___"
2. "I timed the same task by hand vs with ___. Here's the gap."
3. "___ broke this week. Here's what the fix taught me."
4. "The one file that runs my entire ___ workflow"
5. "I gave ___ one rule and it stopped making my worst mistake"
6. "What ___ costs me per month vs what it replaced"
7. "The 5-minute setup I wish I'd done on day one"
8. "My ___ drafted 7 posts. I killed 3. Here's why."
9. "Stop typing the same prompt every day. Do this once instead."
10. "The rule I added after ___ shipped something I never approved"

## Bonus 2: The queue guard

The upgrade nobody adds: make Claude check its own work before you see it. Paste this into the CLAUDE.md from section 3:

```markdown
## Queue guard
Before showing me any draft, check it against voice.md and answer
three questions at the top of the draft file:
1. Does the first line name a specific tool, number, or result?
2. Would I say this sentence to a friend, out loud?
3. Is every claim in it true and mine?
If any answer is no, rewrite before showing me. Show the answers.
```

Now every draft arrives pre-screened, with the screening visible. You review the answers, not the raw output.

## Bonus 3: When it drifts

The three failures everyone hits, and the one-line fix for each:

| What you see | The fix |
|--------------|---------|
| Drafts sound like generic AI | voice.md has too few examples. Paste 3 more real posts you wrote. |
| Statuses change on their own | Add to CLAUDE.md: "Never change a status except idea to drafted." |
| Drafts run long | Add a hard cap: "150 words max per draft. Cut, don't compress." |

---

*I post one Claude Code build a week, follow @build.inquiet. The 56 Hooks guide (how I guard the whole system, not one queue) is free in my store too. When you outgrow this starter, the full Skill Pack is there.*
