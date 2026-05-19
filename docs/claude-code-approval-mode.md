# Claude Code Approval Mode Is Not an Unattended Workflow

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already run **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the finish state: Ralph Workflow is built to hand back a **reviewable result** — a real diff, checks that ran, artifacts, and clear open questions — instead of a session that still depends on you hovering nearby.

Why use it now? Because if your current Claude Code setup still keeps you stuck in approval mode, plan mode, or transcript watching, Ralph Workflow gives you a sharper test: run one real task tonight and decide tomorrow whether the result actually earned a merge.

## Approval mode solves one problem, not the whole one

Claude Code approval mode is useful when you want to:

- block destructive commands until you look
- keep a risky interactive session on a tighter leash
- sanity-check a plan before the tool keeps going
- slow down a run that should not be fully unattended

That is real value.

But it does not automatically answer the harder morning-after questions:

1. **What changed?**
2. **What checks actually ran?**
3. **What still needs judgment?**
4. **Would you merge this?**

If you still have to babysit approvals or reconstruct the night from scrollback, the workflow is not really unattended yet.

## The bottleneck is usually trust in the finish state

When people say they need approval mode, they often really mean one of these instead:

- "I do not trust the run to stop cleanly when it drifts."
- "I do not want to wake up to a confident mess."
- "I need the run to prove what happened without replaying everything."
- "I want a better morning-after merge decision than 'it seemed fine while I watched it.'"

That is not just an approval problem.
That is a finish-state trust problem.

## What a stronger unattended path looks like

A trustworthy overnight run should give you:

- one bounded task
- acceptance criteria before coding starts
- checks that run during the workflow
- a fail-closed handoff if the result is weak or incomplete
- repo-local artifacts you can inspect the next morning

The point is not maximum autonomy for its own sake.
The point is getting back something **cheap to inspect and boring to review**.

## Where Ralph Workflow fits

Ralph Workflow is the better fit when:

- Claude Code is already useful, but you are tired of staying nearby for approvals
- the task is clear enough to hand off overnight
- the real evaluation happens the next morning in the diff, not mid-run in the terminal
- you want the run to end in a reviewable finish state instead of another decision queue

If you need live intervention on a fragile exploratory session, approval mode may still be the right tool.
If you want a bounded overnight handoff you can judge honestly in the morning, Ralph Workflow is the stronger path.

## Simple decision rule

Use this rule before you pick the workflow:

- If the main need is **live approvals during exploration**, stay interactive.
- If the main need is **a reviewable morning-after handoff**, use Ralph Workflow.

## Next steps

- Start with [../START_HERE.md](../START_HERE.md)
- Read [bounded-autonomy-for-unattended-coding.md](./bounded-autonomy-for-unattended-coding.md) if the real fear is drift
- Read [remote-supervision-of-coding-agents.md](./remote-supervision-of-coding-agents.md) if the habit is late-night transcript watching
- Read [review-ai-coding-output-before-merge.md](./review-ai-coding-output-before-merge.md) if the merge decision is still fuzzy
- Inspect the **primary Codeberg repo** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- Use the synced **GitHub mirror** second: <https://github.com/Ralph-Workflow/Ralph-Workflow>
