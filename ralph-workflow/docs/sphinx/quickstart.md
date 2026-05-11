# Quickstart

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Get Ralph Workflow running in an existing project — or a new one — in five minutes.

## Install

```bash
pipx install ralph-workflow
```

Verify the install:

```bash
ralph --version
```

## Initialize Ralph Workflow in a Repository

Navigate to your project directory (must be a git repository), then run:

```bash
cd <your-project>
ralph --init
```

This creates:

- `PROMPT.md` — starter task template in the project root
- `.agent/` — local config files (`ralph-workflow.toml`, `mcp.toml`,
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
4. **Complete** — the pipeline ends successfully once all iterations are exhausted (cap minus completed progress)

Custom policies declared in `.agent/pipeline.toml` can add review, fix, or any other phase.
The default bundled policy is a clean planning → development loop.

Progress is shown inline. If interrupted, Ralph Workflow saves a checkpoint automatically.
When you want to continue from that saved state, run `ralph --resume`.

See [Concepts](concepts.md) for the full phase graph and terminology.

## Where to Go Next

- [Getting Started](getting-started.md) — step-by-step first-run walkthrough with more context
- [Concepts](concepts.md) — terminology and mental models (phases, drains, checkpoints)
- [CLI Reference](cli.md) — all flags and sub-commands
- [Configuration Reference](configuration.md) — config files and precedence
- [Python API Reference](modules.rst) — full Python package documentation

## Related pages

- [Getting Started](getting-started.md) — first-run walkthrough with more context
- [Concepts](concepts.md) — phases, drains, agents, and checkpoints
- [CLI Reference](cli.md) — all flags and sub-commands
- [Configuration](configuration.md) — config files and precedence
