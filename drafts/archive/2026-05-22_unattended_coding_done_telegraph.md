# The Unattended Coding Agent: What "Done" Actually Means

*Published: May 22, 2026*

The most honest test of an AI coding agent is not whether it finishes.

It is what you find when you come back.

---

## The Finish Line Is Not the Output

Most AI coding agents optimize for completion. They write files, run commands, report success.

What they do not always do is end with something you would actually merge.

The gap is not intelligence. It is handoff.

The session ends with a confident summary. You open the diff. You spend an hour untangling what actually changed, which tests actually ran, and whether the result is bounded enough to review honestly.

That is not a workflow. That is a starting point.

---

## What Ralph Workflow Ends With

Ralph Workflow runs existing coding agents — Claude Code, Codex CLI, OpenCode — through a structured loop that ends differently.

Each phase produces:

- **A scoped diff** — what changed, bounded to the task
- **Evidence of checks** — tests that ran, linting that passed
- **A review surface** — readable output you can actually judge
- **Open questions called out** — what Ralph Workflow is unsure about, surfaced honestly

The goal is not maximum output.

The goal is a result you can evaluate in under five minutes and decide: merge, revise, or roll back.

---

## Why the Bounded Diff Is the Real Test

A giant mystery pile of edits is not a win.

A bounded diff tied to a specific task is:

- Scope you can actually review
- Changes you can trace back to the original ask
- A rollback path that is cheap if the result does not hold
- Confidence that the agent ended somewhere useful, not somewhere convenient

The unbounded run looks more impressive while it is running.

The bounded run is more useful the next morning.

---

## The Default Workflow Works as-Is

Ralph Workflow ships with a strong default loop — spec, build, verify, review — that works out of the box on real backlog tasks tonight.

No configuration required to start. No workflow design to do first.

Run it as-is and iterate from the output.

## Extend When the Default Is Not Enough

The default workflow is intentionally simple: one spec phase, one build phase, one verify phase, one review surface.

When that is not the right structure for your task, extend it:

- Add a planning phase for complex work
- Add a separate review phase with its own acceptance criteria
- Add a rollback gate before merge
- Replace any phase with a custom implementation

The core loop does not change. The composition does.

This is what "composable" means in practice: not a library of recipes, but a loop structure you can extend without rebuilding from scratch.

## Use It Tonight

The fastest honest test:

1. Pick one real backlog task — small, bounded, rollback-cheap
2. Write a one-paragraph spec: what changes, what stays, what done looks like
3. Run Ralph Workflow with the default loop
4. Come back and ask: would I merge this?

If yes — give it something bigger next.

If no — you know exactly where the task definition or workflow needs tightening.

Either answer is more useful than a confident summary and a pile of mystery edits.

---

**Ralph Workflow** is free and open source.

Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow  
GitHub mirror: https://github.com/Ralph-Workflow/Ralph-Workflow  
Docs and first-run guide: https://ralphworkflow.com/docs
