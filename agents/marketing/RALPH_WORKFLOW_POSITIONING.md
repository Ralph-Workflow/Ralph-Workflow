# Ralph Workflow Positioning Reference

This file is the durable product-definition reference for marketing and messaging.
Use it when writing README copy, landing-page language, social posts, comparisons, outreach, or review assets.

## 🎯 CANONICAL HERO COPY — USE THIS VERBATIM EVERYWHERE (resolves all prior drift)

Every public surface (site H1, README hero, PyPI summary, social bios) MUST lead with the RESULT,
not the abstraction. There is exactly ONE canonical hero. Do not invent variants.

- **Hero line (H1):** *"Hand your coding agents a spec tonight. Wake up to reviewable, tested commits."*
- **Sub-line (what it is):** *"Ralph Workflow is a free, open-source composable loop framework that runs the coding agents you already use — Claude Code, Codex, or OpenCode — on your own machine. Simple at the center, powerful in composition."*
- **One-sentence pitch (bios/social):** *"Write the spec, run `ralph`, walk away — come back to runnable, tested software you can actually review."*

**RETIRE from hero/primary surfaces:** "The operating system for autonomous coding." Abstract, grandiose,
fails the 10-second test. It may appear once, lower down, as a tagline — never as the lead.

**The star ask (use this reason verbatim, never a bare link):**
> *"Ralph is free and runs locally — stars are the only signal we get that it's working for you, and they set what we build next. If a run shipped real software for you: ⭐ star on Codeberg."*

**Social proof to surface everywhere (both true, currently unused on most surfaces):**
- *"Nightcrawler credits Ralph Workflow as its inspiration."*  ·  *"~1,300 installs/month on PyPI."*

**Always SHOW, never tell:** put a real finish-receipt / unedited terminal output inline at the top of
the surface — do not hyperlink to it off-site. The whole wedge is "lead with the verifiable result."

## What Ralph Workflow is

Ralph Workflow is a free and open-source composable loop framework and AI orchestrator.
It runs the coding agents you already use on your own machine.

It is fundamentally built on Ralph-loop ideas, but it is not just generic "Ralph" and should not be framed that way.
Ralph Workflow is an improvement on Ralph.

## Core conceptual framing

The key idea is simple at the center:
- loop planning
- loop development iteration
- loop the overall process
- keep explicit handoffs between phases
- keep the plan concrete enough to drive the next phase cleanly

That is why Ralph Workflow is more than just "AI orchestration."
It is composable orchestration built around loop structure.

A strong concise framing:
> Ralph Workflow takes the simple Ralph-loop idea and turns it into a composable loop framework for real software engineering.

Another durable framing:
> Ralph Workflow is the operating system for autonomous coding.

The full story should preserve all of these truths together:

- the core Ralph loop is simple
- that simple core composes into complex workflows
- that simplicity makes the system easier to configure, reason about, and extend
- Ralph Workflow is an AI agent orchestrator, not just another coding chat or thin wrapper
- the shipped default workflow is already very good for writing software
- users can start with the default workflow and build on top of it when needed

This should feel like **simple at the center, powerful in composition**.

## What it is for

Ralph Workflow is for big, ambitious, well-specified work.
A good first run should sound like:
- the fitness app you wanted to build
- a major product milestone
- a serious application slice
- a substantial engineering chunk with real acceptance criteria

It is not for:
- small tweaks
- narrow chores
- trivial one-hour edits
- vague product ideas with no spec

## How to evaluate it

The result comes first.
Do not lead with logs.
Do not lead with internal artifacts.
Do not lead with diff language alone.

Lead with:
- what the software does now
- what checks actually ran
- especially integration tests and other real guardrails
- whether the software is runnable, testable, and actually ready
- what a human can verify directly

Logs are fallback evidence only.
Diffs are useful supporting evidence, but not the primary promise.

## Engineering-practice dependency

Ralph Workflow depends on good software engineering practices.
It does not replace them.

Always preserve this framing:
- clear specs matter
- meaningful tests matter
- integration checks matter
- executable software matters
- honest review discipline matters

If a repo lacks those guardrails, Ralph should reflect that honestly instead of pretending the result is more trustworthy than it is.

## Repo / surface hierarchy

- Codeberg is the primary repo and source of truth.
- GitHub is the mirror.
- GitHub must never be treated as the write target for Ralph Workflow source updates.
- Update Codeberg; GitHub follows automatically as the downstream mirror.
- Never manually push to GitHub unless it is the sync script performing the Codeberg→GitHub mirror update.
- Public-facing copy should not accidentally flatten the mirror into the primary.
- When both are shown, frame them as: Codeberg primary, GitHub mirror for GitHub-native inspection/star/watch behavior.

## Messaging guardrails

Do not:
- collapse Ralph Workflow into generic "Ralph"
- describe it as if it were the generic Ralph loop
- position it around small or narrow tasks
- lead public docs with internal agent-to-agent or artifact-plumbing language
- lead with logs as if people should read giant logs first
- lead with artifacts, review bundles, finish receipts, transcript-vs-diff framing, or merge-decision framing as if those are the main product story
- explain internal workflow plumbing before explaining why the product matters to the end user
- imply Ralph magically creates a good testing strategy when the repo does not have one
- treat GitHub like the primary repo when Codeberg is the source of truth

Do:
- frame it as an improvement on Ralph
- frame it as a composable loop framework
- keep the simple-core / serious-engineering contrast
- emphasize that the simple core is what makes more complex workflows easier to build and configure
- emphasize the strong default workflow for writing software
- emphasize that people can use the default first and extend it later
- emphasize ambitious, well-specified work
- emphasize runnable software and real checks
- emphasize result-first evaluation
- present Codeberg as primary and GitHub as the mirror

## Public-docs content boundary

Top-level public docs should not lead with internal mechanics that do not matter to the evaluator or end user.

Usually keep these out of the top-level story unless directly necessary:

- artifacts as a featured concept
- internal handoff structure
- workflow plumbing
- internal phase receipts
- internal agent-to-agent transfer details
- implementation-shaped terminology that mainly matters to maintainers

Those may belong in:

- developer documentation
- architecture documentation
- deep reference pages
- proof/supporting pages that are clearly secondary to the main product story

Top-level/public docs should instead lead with:

- what Ralph Workflow is
- why it is different
- why the simple Ralph-loop core matters
- how that simplicity enables complex composition
- why the default workflow is already strong for writing software
- how users can start with the default and extend it later

## Positioning failure signals

The positioning has drifted if any of these become prominent on top-level surfaces:

- artifact language appears before product language
- “reviewable result” sounds like the main differentiator
- transcript/diff/merge-decision copy dominates the first screen
- internals are explained before user value is clear
- the docs sound like process receipts instead of product positioning
- the product feels more complicated to understand than the underlying core actually is

## Public-docs writing order

When writing public/product docs:

1. explain the simple core first
2. explain composition and orchestration second
3. explain the strong default workflow third
4. explain extension/configuration fourth
5. only then include proof details if they materially help evaluation
