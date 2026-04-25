# Quickstart

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Get Ralph Workflow running in a new project in five minutes.

## Install

```bash
pipx install ralph-workflow
```

Verify the install:

```bash
ralph --version
```

## Initialize a Project

Navigate to your project directory (must be a git repository), then run:

```bash
cd <your-project>
ralph --init
```

This creates:

- `PROMPT.md` — starter task template in the project root
- `.agent/` — local config files (`ralph-workflow.toml`, `mcp.toml`, `agents.toml`,
  `pipeline.toml`, `artifacts.toml`)
- `~/.config/ralph-workflow.toml` and `~/.config/ralph-workflow-mcp.toml` — user-global
  defaults (created once; shared across projects)

## Edit PROMPT.md

Open `PROMPT.md` and replace the example content with your actual task. The file uses
three required sections:

```markdown
# Goal

<one paragraph describing what should be built or fixed>

## Acceptance criteria

- <measurable outcome 1>
- <measurable outcome 2>
```

**Important:** Remove the `<!-- ralph:starter-prompt ... -->` comment at the top of the
file once you have replaced the example content. Ralph Workflow refuses to run while that
sentinel is present so you cannot accidentally run against the placeholder task.

## Verify the Environment

```bash
ralph --diagnose
```

The diagnostic table checks:

- **git** — repository detected, user identity set
- **config** — config files found and valid
- **agents** — AI agents on PATH (e.g., `claude`, `opencode`)
- **MCP** — MCP server definitions valid
- **pre-flight** — PROMPT.md present and edited

Fix any ❌ rows before running the pipeline.

## Run Ralph Workflow

```bash
ralph
```

Ralph Workflow runs the pipeline in phases. At a high level:

1. **Planning** — a planning agent reads PROMPT.md and produces a structured plan artifact
2. **Development** — a developer agent implements the plan, up to `--developer-iters` times
3. **Development analysis** — the pipeline evaluates the development output; loops back to
   development if further iteration is needed, then commits when satisfied
4. **Review** — a reviewer agent inspects the commit and produces an issues artifact
5. **Review analysis** — the pipeline evaluates the review; routes to fix if issues remain,
   then commits again when the review passes

Progress is shown inline. If interrupted, Ralph Workflow saves a checkpoint and resumes
from the last completed phase on the next run.

See [Concepts](concepts.md) for the full phase graph and terminology.

## Where to Go Next

- [Getting Started](getting-started.md) — step-by-step first-run walkthrough with more context
- [Concepts](concepts.md) — terminology and mental models (phases, drains, checkpoints)
- [CLI Reference](cli.md) — all flags and sub-commands
- [Configuration Reference](configuration.md) — config files and precedence
- [API Reference](modules.rst) — full Python package documentation

## Related pages

- [Getting Started](getting-started.md) — first-run walkthrough with more context
- [Concepts](concepts.md) — phases, drains, agents, and checkpoints
- [CLI Reference](cli.md) — all flags and sub-commands
- [Configuration](configuration.md) — config files and precedence
