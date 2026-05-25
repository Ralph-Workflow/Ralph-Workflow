# AI Agent Orchestration CLI: What Matters in Practice

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
That simple center composes into more complex workflows for substantial, well-specified software engineering work on your own machine with the coding agents you already use, and the shipped default is already strong enough to start with.

If you are comparing AI agent orchestration CLIs, the useful question is not whether a tool can call an agent.
The useful question is whether it gives you a workflow that stays understandable, reviewable, and extensible when the task stops being tiny.

## What Ralph Workflow is trying to solve

A single long coding-agent session can work for small edits.
It gets much shakier when the task needs:

- a real written spec
- explicit planning before implementation
- repeated verification instead of one final guess
- room to swap or extend agent behavior later
- a handoff a human can judge without reverse-engineering the whole run

Ralph Workflow takes the simple Ralph-loop idea and uses it as the center of a larger orchestration model.
The point is not complexity for its own sake.
The point is to keep the center simple so the larger workflow stays easier to reason about.

## Why the default workflow matters

The default workflow matters because most users should not have to design an orchestration system before they can test one.
You should be able to start with the shipped path, run a real task, and only then decide whether to extend it.

That is the practical promise: simple at the center, stronger in composition, useful before customization.

## Where to go next

- for the shortest honest first run: [START_HERE.md](../START_HERE.md)
- for task selection help: [first-task-guide.md](./first-task-guide.md)
- for the operator manual: [Sphinx manual home](../ralph-workflow/docs/sphinx/index.rst)
- for configuration and file locations: [configuration.md](../ralph-workflow/docs/sphinx/configuration.md)
