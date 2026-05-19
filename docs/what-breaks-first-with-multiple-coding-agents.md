# What Breaks First When You Run Multiple Coding Agents?

Ralph Workflow is an **open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams who already know how to run Claude Code, Codex, OpenCode, worktrees, or parallel branches — but still do not trust the morning-after result when the task is **too big to babysit and too risky to trust blindly**.

What makes Ralph Workflow different is the finish line: it is built to hand back a **reviewable result** — a real diff, checks, artifacts, and a short handoff trail — instead of a transcript plus a confident claim that the task is done.

Why use it now? Multi-agent coding is getting easier to start and harder to review. You can try Ralph Workflow for free on one real backlog task tonight and decide tomorrow whether the handoff is something you would actually merge.

## The first thing that breaks is usually not Git

When developers say "multiple agents broke," they often do **not** mean a raw merge conflict.

More often, the first serious break is one of these:

- **shared-boundary drift** — one branch changed a schema, config shape, interface, or assumption that another branch quietly depended on
- **reconstruction overhead** — the only way to understand the run is to reread a long terminal transcript
- **weak merged-state confidence** — each branch looked locally fine, but nobody proved the combined result still holds up
- **no clean finish receipt** — you get changed files, but not a short answer to: what changed, what passed, what still needs judgment?
- **scope creep during unattended runs** — the agent kept moving, but the task stopped being small enough to review honestly

Worktrees help with collisions.

They do **not** solve trust on their own.

## The boring guardrails that actually help

If you run more than one coding agent, the safety comes from the handoff discipline:

1. **One owner per shared boundary**
   - If a task touches auth, schemas, build config, or shared interfaces, give one branch clear ownership.
   - Everyone else should consume that boundary or leave notes, not mutate it casually in parallel.

2. **Merged-state checks before merge trust**
   - Do not stop at "my branch CI is green."
   - Rebase, rerun the real checks, and judge the result in the state that would actually land.

3. **A short finish receipt**
   - Every unattended pass should leave a small note saying:
     - what changed
     - what checks ran
     - what failed or stayed uncertain
     - what still needs a human call

4. **Small enough deliverables to review in one sitting**
   - Parallelism only helps when each branch returns a bounded result.
   - If the output is too big to read quickly, the review step becomes the new bottleneck.

5. **A clean morning-after re-entry point**
   - You should be able to open the diff, the artifacts, and the handoff notes without reconstructing the whole night from chat logs.

## What Ralph Workflow changes

Ralph Workflow does **not** replace the agents you already like.

It changes **what comes back**.

The goal is to make unattended multi-agent work end with:

- a sharpened task before coding starts
- scoped execution instead of uncontrolled overlap
- checks that actually ran
- review artifacts saved in the repo
- a finish handoff you can inspect like normal engineering work

That is the difference between "we ran more agents" and "we came back to something we can actually trust enough to review and merge."

## Fastest honest way to evaluate this

Do not test Ralph Workflow on a vague demo.

Test it on one real backlog task that is:

- narrow enough to review in one sitting
- real enough to matter
- clear enough that merged-state checks are meaningful

Then ask one question the next morning:

> **Would I merge this?**

If yes, hand it a harder task next.

If not, tighten the scope or acceptance criteria and run again.

## Next reading

- [START_HERE.md](../START_HERE.md) — shortest path to a real first run
- [which-agent-should-i-start-with.md](./which-agent-should-i-start-with.md) — use the agent already working on your machine
- [claude-code-codex-workflow.md](./claude-code-codex-workflow.md) — cleaner Claude/Codex split without manual glue chaos
- [why-worktrees-are-not-enough.md](./why-worktrees-are-not-enough.md) — why isolation alone does not create a trustworthy finish
- [example-review-bundle.md](./example-review-bundle.md) — inspect a public morning-after handoff before your own run
- [free-open-source-proof.md](./free-open-source-proof.md) — see the proof path and merge test
- [Primary Codeberg repo](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — inspect, star, or watch Ralph Workflow on the main repo
- [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow) — follow the mirror if GitHub is where you already track projects
