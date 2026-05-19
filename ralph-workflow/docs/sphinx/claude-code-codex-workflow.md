# Claude Code + Codex Workflow: Split the Work, Not the Review

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams using tools like **Claude Code** and **Codex** for work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: instead of a transcript and a claim that the task is done, Ralph Workflow is built to leave you with a **reviewable result** — a real diff, checks, artifacts, and a clean morning-after re-entry point.

Why use it now? Because you can keep Claude Code and Codex in your workflow, run one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## Why developers pair Claude Code with Codex

This pairing already makes sense.

A common split is:

- one agent plans or implements
- the other reviews, challenges, or verifies
- the human makes the merge decision at the end

That is usually better than one long unchecked run from a single tool.

## What breaks when the glue is manual

The hard part is usually **not** getting two agents to touch the repo.

The hard part is what happens after that:

- the review loop turns into manual copy-paste glue
- shared boundaries drift across config, schema, or interfaces
- each branch looks locally fine, but the merged state is shaky
- the morning-after handoff is a terminal transcript instead of a clean review surface
- nobody gets a short finish receipt saying what changed, what passed, and what still needs judgment

That is the gap Ralph Workflow is built for.

## A good Claude Code + Codex split

If you are doing this manually today, start simple:

1. pick **one real backlog task** with a clear stopping point
2. let one agent own the implementation path
3. let the second agent challenge the result instead of starting a second competing rewrite
4. run merged-state checks before you trust the finish
5. end with one reviewable handoff, not endless back-and-forth

A useful split is often:

- **Claude Code** for planning and implementation flow
- **Codex** for review, challenge, or second-pass verification

But the exact tool split matters less than the finish line.

The real question is not "which one feels smarter?"

It is:

> **Would I merge what came back?**

## Where Ralph Workflow fits

Ralph Workflow does **not** ask you to switch away from Claude Code or Codex.

It orchestrates the workflow around them so the run is more likely to end with:

- a sharpened task before coding starts
- one bounded implementation pass instead of chaotic overlap
- checks that actually ran
- a review/fix loop inside the workflow
- a clean diff and artifact trail to inspect in the morning
- a short finish receipt instead of a long transcript to reconstruct

In plain terms: Ralph Workflow changes **what comes back**, not the fact that you already like Claude Code or Codex.

## Fastest honest way to try it

Use the agent path that is already installed and already authenticated on your machine.

Then:

1. pick one real task from your backlog
2. write a one-paragraph spec in `PROMPT.md`
3. run Ralph Workflow overnight
4. review the diff, checks, and notes in the morning
5. decide whether you would merge it

If you want the shortest first-run path, start with [Getting Started](getting-started.md).
If you want help choosing the first agent path, read [Which Agent Should I Start With?](which-agent-should-i-start-with.md).
If you want to inspect a public proof asset before your own run, open [Example Review Bundle](example-review-bundle.md).

## The practical takeaway

Claude Code + Codex is already a good instinct.

The bigger question is whether the workflow gives you a **clean, reviewable finish** instead of more supervision work.

That is the job Ralph Workflow is trying to do.
