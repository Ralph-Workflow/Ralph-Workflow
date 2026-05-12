# Quickstart

> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) if you want the same flow with more explanation.

Get Ralph Workflow running in a repo in a few minutes.

## Install

```bash
pipx install ralph-workflow
ralph --version
```

## Initialize Ralph Workflow in a repository

Go to your project directory, then run:

```bash
cd <your-project>
ralph --init
```

This creates:

- `PROMPT.md` — the task file in the project root
- `.agent/` — project-local support files (`mcp.toml`, `pipeline.toml`, `artifacts.toml`)
- `~/.config/ralph-workflow.toml` and `~/.config/ralph-workflow-mcp.toml` — user-global defaults created once and reused across projects

If this repository also needs a project-local copy of the main Ralph Workflow config, run the explicit opt-in local-override flow:

```bash
ralph --init-local-config
```

That command creates `.agent/ralph-workflow.toml` as the project-local main-config override.

## Edit `PROMPT.md`

Open `PROMPT.md` and replace the example with your real task. The starter template has two required sections:

```markdown
# Goal

<one paragraph describing what should be built or fixed>

## Acceptance criteria

- <measurable outcome 1>
- <measurable outcome 2>
```

**Important:** remove the `<!-- ralph:starter-prompt ... -->` comment at the top after replacing the example content. Ralph Workflow refuses to run while that sentinel is still present.

## Verify the environment

```bash
ralph --diagnose
```

The diagnostic checks the repo, config, agent binaries, MCP definitions, and prompt pre-flight state. Fix any ❌ rows before running.

## Run Ralph Workflow

```bash
ralph
```

Ralph Workflow runs unattended and shows progress inline. In plain terms, it plans the task, implements the work, reviews the result during the run, and leaves you with completed work, logs, and artifacts to inspect afterward.

If interrupted, Ralph Workflow saves a checkpoint automatically. Continue from that saved state with:

```bash
ralph --resume
```

## Where to go next

- [Getting Started](getting-started.md) — fuller first-run walkthrough
- [Concepts](concepts.md) — terminology and mental model
- [CLI Reference](cli.md) — all flags and sub-commands
- [Configuration Reference](configuration.md) — config files and precedence
- [Python API Reference](modules.rst) — package documentation
