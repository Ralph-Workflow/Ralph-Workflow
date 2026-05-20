# Claude Code + Codex Workflow: Split the Work, Not the Review

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


Ralph Workflow is an **open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams using tools like **Claude Code** and **Codex** for work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: instead of a transcript and a confident "done" claim, Ralph Workflow is built to leave you with a **strong software result** — a real diff, checks, artifacts, and a clean morning-after re-entry point.

Why use it now? You can keep Claude Code and Codex in your workflow, run one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## Why developers pair Claude Code with Codex

This pairing already makes sense.

A common split is:

- one agent plans or implements
- the other reviews, challenges, or verifies
- the human makes the human review at the end

That is usually better than one long unchecked run from a single tool.

## What breaks when the glue is manual

The hard part is usually **not** getting two agents to touch the repo.

The hard part is what happens after that:

- the review loop turns into manual copy-paste glue
- shared boundaries drift across config, schema, or interfaces
- each branch looks locally fine, but the merged state is shaky
- the morning-after handoff is a terminal transcript instead of a clean review surface
- nobody gets a short result summary saying what changed, what passed, and what still needs judgment

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
- a short result summary instead of a long transcript to reconstruct

In plain terms: Ralph changes **what comes back**, not the fact that you already like Claude Code or Codex.

## Fastest honest way to try it

Use the agent path that is already installed and already authenticated on your machine.

Then:

1. pick one real task from your backlog
2. write a one-paragraph spec in `PROMPT.md`
3. run Ralph Workflow overnight
4. review the diff, checks, and notes in the morning
5. decide whether you would merge it

If you want the shortest first-run path, start with [../START_HERE.md](../START_HERE.md).

If you want help choosing the first agent path, read [which-agent-should-i-start-with.md](./which-agent-should-i-start-with.md).

If you want to inspect a public proof asset before your own run, open [example-review-bundle.md](./example-review-bundle.md).

If this Claude Code + Codex handoff problem is exactly the one you want solved, inspect Ralph Workflow on the [primary Codeberg repo](https://codeberg.org/RalphWorkflow/Ralph-Workflow) and **star or watch it there**. If GitHub is where you already track tooling, the [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow) is there too.

## The practical takeaway

Claude Code + Codex is already a good instinct.

The bigger question is whether the workflow gives you a **clean, reviewable finish** instead of more supervision work.

That is the job Ralph Workflow is trying to do.

## Turn this evaluation into one public Codeberg action

If this split-workflow page fits the pain you are already dealing with, the next step should be public and primary-repo-first.

- **Inspect the primary repo on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star or watch on Codeberg** if Ralph Workflow looks like the cleaner Claude Code + Codex handoff path: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or missing proof on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
- **Need the post-run scorecard first?** Read [After Your First Ralph Workflow Run](./after-your-first-run.md)
