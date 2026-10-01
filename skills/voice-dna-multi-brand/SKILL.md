---
name: voice-dna-multi-brand
description: Extract a reusable voice profile for ONE OR MORE brands. Adds LAW 13 cross-brand contamination check that single-brand Voice DNA skills miss. Use when /voice-dna or when capturing/refreshing writing voice for a multi-brand operator.
tools: Read, Write, Grep
---

# Voice DNA — Multi-Brand

You are extracting reusable voice signatures from a user's existing writing across one or more brands they operate. The output is a reference document per brand AND a cross-brand contamination report. Be analytical. Quote evidence verbatim.

This skill is the multi-brand evolution of Cindie Zhu's single-brand `voice-dna` (published in [7 Claude skills for creative teams](https://www.notion.so/7-Claude-skills-for-creative-teams-full-setup-guide-357eecf169278063ab62c0be04e20149) May 2026). Solo-brand voice extraction is a solved problem. The unsolved problem: when an operator runs 2+ brands with intentional voice separation, single-brand Voice DNA can't surface the leak between them. This skill does.

## Why multi-brand changes the analysis

A solo brand's voice profile lives in the file `~/.claude/voice-profile.md`. Other skills (Content Engine, Repurpose, etc.) read it on every run.

When you run two brands and they share an authoring environment, three failure modes appear that solo Voice DNA can't catch:

1. **Vocabulary bleed** — a word that's canonical in brand A leaks into brand B drafts because the operator (and Claude) already typed it 50 times this week.
2. **Hook-template bleed** — brand A's hook pattern shows up in brand B output because the structural muscle memory is dominant.
3. **Audience-frame bleed** — brand A's "speak to a 9-5 escapee" lens accidentally reframes brand B's "speak to a non-technical builder" piece.

This skill emits a `voice-profile-<brand>.md` PER BRAND plus a `voice-contamination-report.md` flagging every term, hook, or frame that appears in more than one brand's pool with no explicit justification.

## Step 1: enumerate brands

Ask the operator: "Which brands am I extracting voice for? Give me each brand's handle plus the path to its sample pool."

Expected response shape:
- Brand `@brandA`: samples in `~/Documents/brandA-content/` OR pasted inline
- Brand `@brandB`: samples in `~/Documents/brandB-content/` OR pasted inline
- (etc.)

Minimum samples per brand: 10. Warn if fewer; proceed but flag the profile as thin.

## Step 2: per-brand analysis (same axes as Cindie's voice-dna, one pass per brand)

For each brand, run:

1. **Sentence length** — average words per sentence, range, fragment frequency as %.
2. **Vocabulary level** — formality 1-10 with justification. Top 20 distinctive words and bigrams. Skip stopwords.
3. **Tonal markers** — place voice on three axes: formal-casual, warm-edgy, contrarian-agreeable. Quote evidence per axis.
4. **Pacing** — hook openers, mid-piece pivots, sign-offs. Quote 3-5 examples each.
5. **Anti-patterns** — words and moves the brand never uses. Cross-check tics: em dashes, "in today's world", "let's dive in", hashtags, exclamation points.
6. **Signature moves** — 5 to 10 repeatable patterns unique to this brand. Quote one occurrence each.
7. **Audience address** — frequency of "you" vs "we" vs "I". Direct vs indirect.
8. **Emotional range** — which emotions appear, which are absent. One quoted example per present emotion.

Save per brand to `~/.claude/voice-profile-<brandhandle>.md`. Overwrite if it exists.

## Step 3: cross-brand contamination scan (the multi-brand-only step)

Compare every brand's pool to every other brand's pool. For each pairwise comparison:

1. **Vocabulary overlap** — list the top 20 distinctive words for brand A that also appear ≥3 times in brand B's pool. Format: `term | brand-A-canon-count | brand-B-leak-count | justified? (Y/N)`.
2. **Hook-pattern overlap** — list any hook opener template (e.g., "I built X that…") that appears in both brands. Format: `template | brand-A-uses | brand-B-uses | justified? (Y/N)`.
3. **Audience-frame overlap** — list any sentence where the audience-frame from one brand appears in another brand's piece (e.g., a brand-A "9-5 escapee" frame appearing in a brand-B "non-technical builder" piece).

For each row, determine `justified` by checking:
- Is the term/template/frame in BOTH brand's `rules/content-voice-*.md` doctrine files? If yes, justified.
- Is there a documented exception (e.g., "Default Shift hook is a doctrine-approved shared pattern, attribution required")? If yes, justified.
- Otherwise: contamination. Flag.

Save to `~/.claude/voice-contamination-report.md`. Include a one-line summary at top: `N flagged contaminations across M brand pairs; review before next drafting session.`

## Step 4: print actionable next steps

Print to chat:
- Path to each per-brand profile written.
- Path to the contamination report.
- Top 3 contamination rows (highest-leak-count) with a one-line fix suggestion per row.
- If contamination count > 10: recommend `/voice-dna-multi-brand` re-run after the operator scrubs the leak surface.

## Calling pattern

```
/voice-dna-multi-brand
```

The skill will ask for brand list. Pass paths or paste inline.

## Why this exists

The brand-separation rule (LAW 13 in some doctrines, or just operator discipline in others) is fragile because vocabulary and hook patterns are sticky in working memory. The contamination report catches the leaks that no human reviewer flags because they're statistical, not narrative.

## Reference implementation

This SKILL.md is the spec. The actual analysis runs in Claude — no external API, no tools beyond Read/Write/Grep. Pure prompt-based reasoning on sample text.

For the single-brand precursor (Cindie Zhu's `voice-dna`), see her [public Notion guide](https://www.notion.so/7-Claude-skills-for-creative-teams-full-setup-guide-357eecf169278063ab62c0be04e20149).
