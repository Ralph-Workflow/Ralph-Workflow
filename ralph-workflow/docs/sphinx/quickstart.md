# Quickstart

Use this page when you already understand the product story and want the shortest path to one honest first run in a real repository.
Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core, and that simple core composes into a stronger workflow system you can use as a default workflow before you customize anything.
If you need config answers, open [Configuration Reference](configuration.md). If you want docs routed by use case, open [End-User Stories](user-stories.md).

Bring your existing coding agents. Keep your existing setup. Keep your keys to yourself.
Ralph Workflow is meant to orchestrate the tools you already trust, not become the default home for your provider secrets.

The point of this page is to use that default safely before you build on top of it.
If you need fuller explanation, task-selection help, or more context for why the default flow works, go back to [Getting Started](getting-started.md).

## Quickstart checklist

1. Pick a real repo and a task with a visible finish line.
2. Initialize the repo with `ralph --init`.
3. Prefer the default workflow before touching advanced config.
4. Run Ralph Workflow on that task.
5. Judge the result by the repo change and the checks that ran.
6. Only customize after you know what the default loop already does well enough.

For explicit project-local overrides, run `ralph --init-local-config` and then edit `.agent/ralph-workflow.toml` in that repo.
That local file belongs to the opt-in override flow, not the default `ralph --init` path.

## Good first-task shape

Your first task should be:

- substantial enough to benefit from planning and verification
- specific enough that success is easy to recognize
- connected to checks you can actually run
- important enough that better unattended workflow would matter

If you need help picking that task, use [First Task Guide](first-task-guide.md).

## What success should look like

A good first quickstart run should leave you with:

- a visible repo change tied to the task you asked for
- checks or verification output you can inspect directly
- a short list of remaining risks or follow-up work
- enough confidence to decide whether the default workflow is useful in this repo

## After the quickstart

- Need the fuller first-run walkthrough? Open [Getting Started](getting-started.md).
- Need config answers? Open [Configuration Reference](configuration.md).
- Need docs routed by use case? Open [End-User Stories](user-stories.md).
- Need to inspect trustworthy output? Open [What Good Ralph Workflow Output Looks Like](reviewable-output.md).
