# Remote Supervision of Coding Agents

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already run **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is not that it gives you another dashboard to stare at. Ralph Workflow is built so you can step away and still come back to a **strong software result**: a real diff, checks that ran, artifacts, and enough context to decide whether the work earned a merge.

Why use it now? Because if your current workaround is remote supervision, approval babysitting, or late-night transcript watching, Ralph Workflow gives you a cleaner test: run one real task tonight and decide tomorrow whether the result actually earned a merge.

## The real problem is usually not lack of visibility

When people ask for remote supervision of coding agents, they often mean one of these instead:

- "I do not trust the finish state yet."
- "I do not want to wake up to a confident mess."
- "I need the run to stop cleanly when it leaves the brief."
- "I want proof of what changed without replaying the whole session."

That is a finish-state problem, not just a viewing problem.

## What remote supervision is good for

Remote supervision helps when you need to:

- watch a long interactive session in progress
- intervene on a risky step immediately
- inspect live behavior during exploration or debugging
- keep an eye on approval-heavy work

That is useful.
But it still leaves a second question unresolved:

> When the run ends, do you have something reviewable — or just something observed?

## What a trustworthy unattended run should hand back

A good morning-after handoff should make four things obvious:

1. **What changed**
2. **What checks ran**
3. **What still needs judgment**
4. **Whether you would merge it**

If remote supervision gives you awareness but not those four answers, it is not solving the main trust gap.

## Ralph Workflow's angle

Ralph Workflow is for the cases where you want to stop supervising every minute and start judging the outcome honestly.

That means:

- one bounded task
- explicit acceptance criteria
- real verification during the run
- a fail-closed handoff when the task is incomplete
- repo-local artifacts you can inspect the next morning

The point is not maximum autonomy.
The point is getting back a result that is **cheap to inspect and boring to review**.

## When to prefer Ralph over pure remote supervision

Ralph Workflow is the better fit when:

- the task is clear enough to hand off overnight
- you care more about the final review surface than live session theater
- you want the run to fail closed instead of drifting silently
- you need a stronger human review than "I watched it for a while and it seemed okay"

If you mainly need live observation for an exploratory or fragile session, remote supervision can still be the right tool.
If you need a bounded overnight handoff you can judge in the morning, Ralph Workflow is the stronger path.

## A simple decision rule

Ask this before choosing the workflow:

- If the main need is **live intervention**, use supervision.
- If the main need is **a reviewable finish state**, use Ralph Workflow.

Many teams need both at different times. The mistake is assuming supervision alone solves the finish-state trust problem.

## Next steps

- Start with [../START_HERE.md](../START_HERE.md)
- Read [bounded-autonomy-for-unattended-coding.md](./bounded-autonomy-for-unattended-coding.md) if the real risk is drift
- Read [review-ai-coding-output-before-merge.md](./review-ai-coding-output-before-merge.md) if the human review is still fuzzy
- Inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- Use the synced **GitHub mirror** second: <https://github.com/Ralph-Workflow/Ralph-Workflow>
