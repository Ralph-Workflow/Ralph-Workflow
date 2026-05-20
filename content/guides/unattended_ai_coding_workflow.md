# Unattended AI Coding Workflow

If you want an AI coding agent to work while you sleep, the question is not "can it write code?"

The real question is:

**will it leave you something you can trust in under five minutes tomorrow morning?**

That is the difference between a fun demo and a workflow.

## What "unattended" should mean

A real unattended run should hand back:

- a scoped diff
- checks that actually ran
- a short receipt of what changed
- explicit open questions or risk notes
- a clear point where a human decides whether to merge

If the result is just a long transcript, the run was not really unattended. You still have to reconstruct what happened.

## Why most overnight agent runs disappoint

The usual failure is not model intelligence.

It is one of these:

1. **The task was too broad**
2. **"Done" was never defined before the run started**
3. **The agent stopped at confidence instead of verification**
4. **The morning artifact was a chat log instead of a review bundle**

That is why Ralph Workflow centers the finish line instead of the prompt theater.

## What Ralph Workflow changes

Ralph Workflow is a free and open-source CLI for running AI coding agents through a reviewable loop.

It is built around three ideas:

1. **Sharpen the task before code starts**
2. **Keep implementation and verification in the same loop**
3. **End with evidence, not just a claim**

That means the goal is not maximum autonomy at any cost.

The goal is a result you can open in the morning and judge quickly.

## Who this is for

Ralph Workflow fits best if you are:

- a developer with a real backlog
- a technical founder who wants bounded overnight progress
- a team that needs reviewable output instead of transcript archaeology
- already using tools like Claude Code, Codex, or OpenCode and wants stronger process around them

## Best first task shapes

Start with work that is easy to verify:

- one validation rule
- one feature slice
- one bounded refactor with tests
- one backlog task with clear acceptance criteria

If you need help choosing, use [Good unattended task vs bad one](./good_unattended_task.md).

## Where to inspect the project

**Primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

**GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

Use Codeberg as the main project home for stars, watches, issues, and PRs.

## The honest evaluation question

Run one real task.

Then ask:

**Would I merge this?**

If yes, the workflow is doing its job.

Next:

- [Start here on one real task](../../START_HERE_RALPHWORKFLOW.md)
- [Spec-driven AI agent guide](./spec_driven_ai_agent.md)
- [Review bundle example](../examples/review_bundle_example.md)
