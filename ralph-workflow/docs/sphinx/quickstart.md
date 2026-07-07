# Quickstart

Ralph Workflow is **the autopilot for coding agents** — a free and open-source operating system for autonomous coding, an AI agent orchestrator built around a simple Ralph-loop core that becomes powerful through composition.
**Hand it a well-specified coding task, let the agents plan, build, verify, and fix, and come back to reviewable, tested work.**
The default workflow is strong enough to adopt as-is, before you customize anything.

If you need config answers, open [Configuration Reference](configuration.md). If you want docs routed by use case, open [End-User Stories](user-stories.md).

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

## Initialization commands

Use the default initializer first:

```bash
ralph --init
```

`ralph --init` scaffolds `PROMPT.md` plus the standard project-local support files used for MCP, pipeline, and artifact configuration. It also installs Ralph Workflow's mirrored default skill bundle from the shipped package assets and prints a Baseline Capabilities summary showing the health of all default helpers. The first run also installs the bundled skill bundle into `~/.claude/skills/` and creates symlinks into Codex, OpenCode, and AGY skill roots, so all supported agents can discover Ralph Workflow skills automatically. Re-running `ralph --init` is idempotent and re-checks every skill root plus refreshes the default .gitignore. Pi.dev is wired as a transport but has no documented skill-discovery system per <https://pi.dev/docs/latest/usage>, so no Pi user-global install target is created and no `.pi/skills/` directory is written. If you explicitly want a project-local override copy of the main config, create `.agent/ralph-workflow.toml` with:

```bash
ralph --init-local-config
```

That explicit opt-in path is the right place for repo-local main-config overrides; the broader file layout is explained in [Configuration Reference](configuration.md).

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
- Need to inspect trustworthy output? Open [Example Review Bundle](example-review-bundle.md).
