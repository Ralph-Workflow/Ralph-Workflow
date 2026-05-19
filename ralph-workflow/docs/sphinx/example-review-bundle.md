# Example Review Bundle: What a Morning-After Ralph Workflow Handoff Looks Like

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: not just a transcript and a claim that the task is done, but a **reviewable result** with scoped files, checks, and artifact notes.

Why look at this now? Before you install anything, you can inspect the exact shape of a small first-run handoff and decide whether it looks like something you would actually trust yourself to review.

## The live example in this repo

Open the example bundle here:

- [examples/first-review-bundle/README.md](../../../examples/first-review-bundle/README.md)
- [examples/first-review-bundle/PROMPT.md](../../../examples/first-review-bundle/PROMPT.md)
- [examples/first-review-bundle/.agent/DEVELOPMENT_RESULT.md](../../../examples/first-review-bundle/.agent/DEVELOPMENT_RESULT.md)
- [examples/first-review-bundle/.agent/ISSUES.md](../../../examples/first-review-bundle/.agent/ISSUES.md)
- [examples/first-review-bundle/.agent/FIX_RESULT.md](../../../examples/first-review-bundle/.agent/FIX_RESULT.md)
- [examples/first-review-bundle/.agent/artifacts/](../../../examples/first-review-bundle/.agent/artifacts/)

If that bundle looks like the kind of morning-after handoff you would actually trust, take the next public step on **Codeberg**:

- **Inspect the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star / watch the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction or proof gaps:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## What this example proves

This bundle is intentionally small.

It shows a first task that is:

- real enough to matter
- narrow enough to review quickly
- easy to verify
- cheap to roll back if it misses

The task is simple: reject empty or whitespace-only project names in a CLI before any files are created.

That lets you judge the thing that matters most: not whether the agent looked clever, but whether the handoff is clear.

## How to review it in under five minutes

1. Read the prompt and acceptance criteria.
2. Open `DEVELOPMENT_RESULT.md` and check whether the changed files match the ask.
3. Open `ISSUES.md` and `FIX_RESULT.md` to see whether review feedback tightened the result.
4. Glance at the JSON artifacts to see the machine-readable trail.
5. Ask one question: **would I merge this?**

If that review path feels boring and fast, the workflow is doing its job.
If it feels vague, the handoff is still too weak.

## Next steps

- Start with [../START_HERE.md](../../../START_HERE.md)
- See [reviewable-output.md](./reviewable-output.md) for the shorter proof overview
- Use [first-task-prompt-templates.md](./first-task-prompt-templates.md) if you want a copy-paste `PROMPT.md` shape for your own first run
- If this proof asset makes Ralph Workflow look worth trying, put the adoption signal on **Codeberg** first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
