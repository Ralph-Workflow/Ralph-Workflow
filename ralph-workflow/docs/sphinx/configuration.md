# Configuration Reference

> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) before diving into config details.

Ralph Workflow uses layered configuration. Settings are resolved in this order, highest priority first:

1. **CLI flags**
2. **Project-local config** — `.agent/ralph-workflow.toml`
3. **User-global config** — `~/.config/ralph-workflow.toml`
4. **Bundled defaults** — shipped in `ralph/policy/defaults/`

## The files most operators care about

Ralph Workflow manages a standard config set across two scopes.

### User-global files

| File | Purpose |
|------|---------|
| `~/.config/ralph-workflow.toml` | Global defaults: agent selection, iteration counts, verbosity |
| `~/.config/ralph-workflow-mcp.toml` | MCP server definitions shared across projects |
| `~/.config/ralph-workflow-pipeline.toml` | Global pipeline defaults when a workspace has no local pipeline override |
| `~/.config/ralph-workflow-artifacts.toml` | Global artifact defaults when a workspace has no local artifact override |

### Project-local files

| File | Purpose |
|------|---------|
| `.agent/mcp.toml` | Project-specific MCP server definitions |
| `.agent/pipeline.toml` | Workflow phases, routing, and parallel settings |
| `.agent/artifacts.toml` | Artifact type schemas and contracts |
| `.agent/ralph-workflow.toml` | Optional project-specific overrides for agents, chains, drains, and main settings |

Run `ralph --init` to create the standard project-local support files. Use `ralph --init-local-config` when you explicitly want a project-local copy of the main config.

## Bundled defaults

The bundled defaults live in `ralph/policy/defaults/`. When in doubt, the files themselves are the most exact reference:

- `ralph-workflow.toml` — main config
- `mcp.toml` — MCP server config
- `pipeline.toml` — workflow phases and routing
- `artifacts.toml` — artifact contracts

## Common settings in `ralph-workflow.toml`

The main config file is `~/.config/ralph-workflow.toml`, with optional project-level overrides in `.agent/ralph-workflow.toml`.

### `[general]`

Core workflow settings: verbosity, git identity, retry behavior, and liveness limits.

| Key | Default | Description |
|-----|---------|-------------|
| `verbosity` | `2` | Output verbosity: 0=quiet, 1=normal, 2=verbose, 3=full, 4=debug |
| `git_user_name` | (from git config) | Git author name for commits |
| `git_user_email` | (from git config) | Git author email for commits |
| `max_retries` | `3` | Max retries per agent attempt when synthesized from the main config |
| `retry_delay_ms` | `1000` | Base delay between retries |
| `backoff_multiplier` | `2.0` | Exponential backoff multiplier |
| `max_backoff_ms` | `60000` | Maximum retry backoff delay |
| `max_cycles` | `3` | Maximum full fallback cycles through a drain |
| `agent_idle_timeout_seconds` | `300.0` | Max idle seconds before a stalled agent is terminated |

### `[general.workflow]`

| Key | Default | Description |
|-----|---------|-------------|
| `checkpoint_enabled` | `true` | Enable checkpoint/resume support |

## Agent chains and drains

Most operator customization happens in `[agent_chains]` and `[agent_drains]` inside `ralph-workflow.toml`.

```toml
[general]
max_retries = 3
retry_delay_ms = 1000

[agent_chains]
planning = ["claude/opus"]
development = ["opencode/minimax/MiniMax-M2.7-highspeed", "codex", "claude/sonnet"]
analysis = ["opencode/openai/gpt-5.4"]
commit = ["claude/haiku"]

[agent_drains]
planning = "planning"
planning_analysis = "analysis"
development = "development"
development_analysis = "analysis"
development_commit = "commit"
```

In practice:

- **chains** define fallback order for one kind of work
- **drains** map workflow steps to those chains

Multiple drains can point at the same chain. That lets you change agent policy without rewriting the workflow itself.

## `pipeline.toml` in plain language

`pipeline.toml` defines the workflow shape Ralph Workflow uses for a run.

The top-level ideas are:

- `entry_phase` — where the run starts
- `terminal_phase` — what counts as successful completion
- `[phases.<name>]` — the individual steps in the workflow
- transitions — where Ralph Workflow goes next on success, failure, or loopback
- counters and budgets — how Ralph Workflow limits iteration and retry behavior
- post-commit routes — what happens after a commit-producing step
- parallel execution — whether independent work units can fan out concurrently

### Development proof policy

The development phase now supports a proof policy block in `pipeline.toml`:

```toml
[phases.development.artifact_proof_policy]
require_plan_proof = true
require_analysis_proof = true
```

Omitting this block inherits the bundled defaults. To disable proof enforcement in a project-local `.agent/pipeline.toml`, set both fields to `false` explicitly. The proof policy is phase-owned, so it lives under `[phases.development]` alongside the other phase settings.

## Advanced sections you may not need right away

The main config also supports deeper transport-specific and workflow-authoring sections such as:

- `[ccs]` / `[ccs_aliases]` for explicitly-headless Claude Code Switch defaults
- `[agents.*]` for agent defaults, including `transport = 'claude_interactive'` on the built-in `claude` path
- loop counters and budget counters
- review-role bypass routes
- recovery policy tuning
- parallel fan-out controls

Those sections are useful when you are customizing Ralph Workflow deeply, but many operators never need to touch them. Use `claude-headless` or CCS when you explicitly want the documented non-interactive Claude path.

## When to read further

Use the more detailed docs when you need them:

- [Concepts](concepts.md) — terms like phase, drain, and artifact
- [CLI Reference](cli.md) — runtime flags and shortcuts
- [Policy Explanation](policy-explanation.md) — inspect the active workflow in plain English
- [Developer Reference](developer-reference.md) — implementation-oriented detail
