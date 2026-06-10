# H4 ACTUATION: frankbria/ralph-claude-code "See also" section request

**Date staged:** 2026-06-10 21:55 GMT+2 (evaluator turn)
**Target:** https://github.com/frankbria/ralph-claude-code/issues
**Maintainer activity:** last push 2026-06-10 (active today)
**Maintainer pattern:** responds to feature/enhancement issues, has "Mentioned in Awesome Claude Code" badge (ecosystem-aware)
**Title (proposed):** "Consider a 'See also / Related projects' section in the README? (3 peer ralph projects worth a one-line mention)"

## Issue body draft (engineer-voice, non-pitch, 5 paragraphs, ONE codeberg link)

Title: `Consider a "See also / Related projects" section in the README? (3 peer ralph projects worth a one-line mention)`

Body:

> Big fan of this project — 9.3k★ is well-earned, and the loop-orchestration story is the cleanest I've seen in the ralph-* family. Two of the other projects in this space are quietly building complementary pieces that ralph-claude-code users probably want to know exist; a one-line "See also" link would surface them to your fork base.
> 
> The three I'd consider linking (in addition to Geoffrey Huntley's [ghuntley.com/ralph](https://ghuntley.com/ralph/), which is already in your README via the badge):
> 
> 1. **Ralph Workflow** (codeberg.org/RalphWorkflow/Ralph-Workflow) — composable loop framework with explicit plan / build / verify phases. Different execution model (agent-agnostic vs Claude-Code-specific), same overnight-reviewable pattern. Has a SPEC layer called "spec library" that solves the context-cluttering problem your README hints at under "Context management."
> 
> 2. **speq-skill** (github.com/marconae/speq-skill) — spec-driven skill for Claude Code / Codex that adds semantic-anchors (TDD / BLUF / ADR) and a "permanent and growing spec library" that complements the execution loop. The maintainer's blog (deliberate.codes) has a post on writing specs for AI agents that pairs naturally with the loop design here.
> 
> 3. **endario/unattended-loop** (github.com/endario/unattended-loop) — Claude Code skill wrapping `/loop` with a 4-file harness + TDD/review/PR-first workflow + 3-strike failure budget. Adjacent to your project: same ICP, different default-workflow shape.
> 
> All three are MIT/Apache/equivalent, all are independently-maintained, none of them compete on the Claude-Code-specific execution model you've built. A one-line "See also" link block in the README would (a) help your fork-base find genuinely complementary tooling, (b) acknowledge the wider ralph ecosystem without false equivalence, and (c) potentially be reciprocated — the three maintainers already have Ralph-Clauders in their "Related" lists in various forms.
> 
> Happy to draft the README section if useful, or leave it to you — either way thanks for the readme that taught me the ralph loop in the first place.

## Why this is the right H4 actuation (not the original thebasedcapital/nightcrawler target)

The MARKETING_COVERAGE_MAP.md H4 listed thebasedcapital/nightcrawler as the co-publish target. But:
- thebasedcapital/nightcrawler has 0 open issues, 4★, 2 forks, **last push 2026-02-23 (3.5 months ago)** — the maintainer is quiet and an issue on their repo will not be seen quickly.
- frankbria/ralph-claude-code is the SAME archetype (ralph-looper in the same ICP) but with 9.3K★, 705 forks, 16 open issues, **last push 2026-06-10 (today, active)**.
- The "see also" framing is a CATEGORY-LEVEL question that any active maintainer would consider (low-friction, non-feature, no commitment).
- The expected signal (a README "See also" block) is the same Nightcrawler-pattern credit that historically moved Ralph stars.

## What we are NOT doing

- Not asking for a backlink-only PR (the maintainer's existing issue list is feature-PRs; this is a non-code meta-issue, which is fine — many maintainers prefer the meta-issue form for "consider changing the README").
- Not pitching Ralph Workflow as the one true ralph — naming three projects and ranking them in the same way the maintainer would rank them is more credible.
- Not adding a contact signature or persona tag (per OUTREACH_COPY_CONTRACT).
- Not cross-posting to any other ralph-* repo this run (cap 1 such engagement/run per D17 cap).

## Check-back

+5d: if frankbria responds, AMPLIFY (star-back, reply with concrete value, cross-post on social). If silent, the issue is still a public record of the category relationship.
