# Configuration Reference

> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) before diving into config details.

Use this page when your question is about files, precedence, validation commands, or configuration edits.
This page answers the operator question quickly: which file do I edit, at which scope, and how do I validate the change safely?
Ralph Workflow keeps the core simple, but the docs should still point you straight to the right TOML file instead of making you reverse-engineer the policy layout.
If you want docs routed by use case instead of page type, open [End-User Stories](user-stories.md).

Bring your existing coding agents and keep your keys to yourself.
Most operators mainly need to wire Ralph Workflow into agent CLIs they already trust, not re-home their model secrets.

If your immediate question is **"Where do I edit `ralph-workflow.toml`?"**, the short answer is:

- **Global defaults for all projects** → `~/.config/ralph-workflow.toml`
- **Project-specific override for just this repo** → `.agent/ralph-workflow.toml`

If `.agent/ralph-workflow.toml` does not exist yet, create it with:

```bash
ralph --init-local-config
```

After editing config, validate it with:

```bash
ralph --check-config
ralph --check-policy
```

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

## Advanced config map

If you already know you want the deeper docs, use this map instead of scanning the whole manual:

| I want to change... | Open this page |
|---|---|
| agent selection, retry behavior, verbosity, or drain bindings | this page: [Configuration Reference](configuration.md) |
| workflow phases, loopbacks, commit routes, fan-out, counters, or recovery | [Advanced Pipeline Configuration](advanced-pipeline-configuration.md) |
| artifact contracts, decision vocabularies, summary files, or commit-message artifacts | [Advanced Artifact Configuration](advanced-artifact-configuration.md) |
| MCP servers, web search, crawl, or media/web-visit integrations | [Advanced MCP Configuration](advanced-mcp-configuration.md) |
| what the active policy means after all config layers resolve | [Policy Explanation](policy-explanation.md) |

## Which file should I edit?

Use this rule of thumb:

- **I want this behavior in every repo I run** → edit `~/.config/ralph-workflow.toml`
- **I only want this behavior in one repo** → edit `.agent/ralph-workflow.toml` **unless the change is about workflow shape, phases, or loopbacks**
- **I want to change workflow phases, loopbacks, counters, or phase-owned policy** → edit `.agent/pipeline.toml`, then read [Advanced Pipeline Configuration](advanced-pipeline-configuration.md)
- **I want to change MCP servers or web/search access** → edit `~/.config/ralph-workflow-mcp.toml` or `.agent/mcp.toml`
- **I want to change artifact contracts/history** → edit `.agent/artifacts.toml`

The common mistake is editing `ralph-workflow.toml` when the real change belongs in `pipeline.toml`. The main `ralph-workflow.toml` file is mostly for:

- agent selection and fallback chains
- drain-to-chain bindings
- retry / timeout / verbosity settings
- Claude Code Switch / agent definitions

The workflow structure itself lives in `pipeline.toml`.

## The fastest safe workflow for editing config

1. Decide whether the change is **global** or **repo-local**.
2. Edit the right TOML file.
3. Run `ralph --check-config`.
4. If you changed workflow behavior, also run `ralph --check-policy`.
5. Run `ralph --diagnose` before the next real unattended run.

If you want the active workflow explained in plain English after the config change, run:

```bash
ralph --explain-policy
```

## Most common user edits in `ralph-workflow.toml`

Most end users do not need to invent a policy from scratch. They usually want one of these changes:

1. change which agents are used for planning / development / analysis / commit
2. increase or decrease retry / cycle behavior
3. raise or lower verbosity
4. set git author info for automated commits
5. opt out of anonymous metadata-only telemetry
6. create a project-local override without affecting every repo

## Bundled defaults

The bundled defaults live in `ralph/policy/defaults/`. When in doubt, the files themselves are the most exact reference:

- `ralph-workflow.toml` — main config
- `mcp.toml` — MCP server config
- `pipeline.toml` — workflow phases and routing
- `artifacts.toml` — artifact contracts

## Environment variables

Ralph Workflow honours a small set of environment variables. These are inputs to the engine, not extensions of the 60-second combined test budget.

### Pro integration contract

The Pro↔Ralph Workflow contract uses exactly three engine-facing variables (see `ralph/pro_support/env.py`):

| Variable | Purpose |
|----------|---------|
| `PROMPT_PATH` | Absolute or relative path to the operator-authored run spec. When set, Ralph Workflow prefers this over `<workspace>/PROMPT.md`. This is the canonical way to point the engine at a non-default prompt file. |
| `RALPH_WORKFLOW_PRO` | Non-empty truthy marker that the engine is running as a Ralph Workflow Pro subprocess. |
| `RALPH_WORKSPACE` | Absolute or relative path to the workspace root. When set, Ralph Workflow prefers this over the current working directory when resolving the workspace scope. |

### Operator/runtime variables

| Variable | Purpose |
|----------|---------|
| `RALPH_AGY_BINARY` | Path to a custom `agy` executable, or to the deterministic mock at `tests/_support/mock_agy.sh` for CI. See [CLI Reference](cli.md) and [Agent Compatibility](agent-compatibility.md). |
| `RALPH_INLINE_SKILLS_DIR` | Directory whose skill files are inlined into prompts through `SKILLS_INLINE_CONTENT` instead of relying on the shipped skill bundle. See [Prompts](prompts.md). |
| `XDG_CONFIG_HOME` | When set, Ralph Workflow places the user-global config at `$XDG_CONFIG_HOME/ralph-workflow.toml` instead of `~/.config/ralph-workflow.toml`. |

### Test-only timeout variables

| Variable | Purpose |
|----------|---------|
| `RALPH_PYTEST_TEST_TIMEOUT_SECONDS` | Per-test timeout passed by the `ralph.verify_timeout` wrapper. |
| `RALPH_PYTEST_SUITE_TIMEOUT_SECONDS` | Per-suite invocation timeout passed by the `ralph.verify_timeout` wrapper. |

These timeout variables are set by the test harness; they do **not** extend the 60-second combined budget enforced by `make verify`. See [Verification Model](verification-model.md) for the non-circumvention rule.

## Common settings in `ralph-workflow.toml`

The main config file is `~/.config/ralph-workflow.toml`, with optional project-level overrides in `.agent/ralph-workflow.toml`.

### `[general]`

Core workflow settings: verbosity, git identity, retry behavior, and liveness limits.

| Key | Default | Description |
|-----|---------|-------------|
| `verbosity` | `2` | Output verbosity: 0=quiet, 1=normal, 2=verbose, 3=full, 4=debug |
| `telemetry_enabled` | `true` | Anonymous metadata-only telemetry is enabled by default. Set to `false` to opt out from user-global or project-local `ralph-workflow.toml`. Ralph Workflow uses the data only to improve reliability/product quality, understand active usage and feature adoption, and inform users about useful capabilities. It is never sold, rented, or shared for advertising. |
| `git_user_name` | (from git config) | Git author name for commits |
| `git_user_email` | (from git config) | Git author email for commits |
| `max_retries` | `3` | Max retries per agent attempt when synthesized from the main config |
| `retry_delay_ms` | `1000` | Base delay between retries |
| `backoff_multiplier` | `2.0` | Exponential backoff multiplier |
| `max_backoff_ms` | `60000` | Maximum retry backoff delay |
| `max_cycles` | `3` | Maximum full fallback cycles through a drain |
| `agent_idle_timeout_seconds` | `300.0` | Max idle seconds before a stalled agent is terminated |
| `agent_idle_activity_evidence_ttl_seconds` | `30.0` | Per-channel activity TTL: while any non-stdout channel (`mcp_tool`, `subagent`, `workspace`) is fresher than this, the `NO_OUTPUT_DEADLINE` fire is deferred and the watchdog returns `CONTINUE`. Workspace evidence is collected whenever a run has a `workspace_path`, even when the progress UI is disabled. A subagent process that is alive but silent on every channel is **not** treated as activity. Set to `0.0` to opt out and restore the legacy stdout-only behaviour. See the `## Idle watchdog` section in the README for the four-channel model. |
| `agent_workspace_change_weights` | `{ source = 1.0 }` | Per-kind workspace file-change weights used by the activity-aware watchdog. Each kind (`source`, `log`, `cache`, `artifact`, `other`) is binary: `1.0` lets the change defer `NO_OUTPUT_DEADLINE`, `0.0` drops it. The default only weights `source` (source code and documentation). Operators who previously relied on log-file activity can opt in with `agent_workspace_change_weights = { source = 1.0, log = 1.0 }`. See [Watchdogs and Timeouts](watchdogs-and-timeouts.md) for the full migration note. |

### Example: change verbosity globally

```toml
[general]
verbosity = 3
```

Use this when you want richer logs in every project.

### Example: opt out of telemetry globally

```toml
[general]
telemetry_enabled = false
```

Ralph Workflow sends anonymous metadata-only telemetry by default: random
installation/session IDs, runtime/platform metadata, versions, command names
from a closed vocabulary, coarse session timing/outcome, coarse UTC usage
buckets, phase-role aggregates, Sentry release-health sessions, tracing,
breadcrumbs, and custom metrics derived from those same closed metadata
fields. Sentry automatic integrations are disabled, so tracing is limited to
the manual `ralph.session` transaction and metadata-only events. It does not
send prompts, model output, logs, profiling stack samples, codebase paths,
hostnames, usernames, raw phase names, environment-variable values, full
timestamps, or timezone names.

The purpose is to improve reliability and product quality, understand active
users and feature adoption, and help operators learn about useful product
capabilities. The data is never sold, rented, or shared for advertising. You
can also opt out per invocation with `RALPH_DISABLE_TELEMETRY=1`, or set the
same `telemetry_enabled = false` key in `.agent/ralph-workflow.toml` for a
project-local opt-out.

### Example: set git author info globally

```toml
[general]
git_user_name = "Your Name"
git_user_email = "you@example.com"
```

Generated commits still append a Ralph Workflow co-author trailer:

```text
Co-authored-by: Ralph Workflow <noreply@ralphworkflow.com>
```

That keeps the primary author identity yours while still marking commits that Ralph Workflow generated.

### Example: make one repo quieter without changing everything else

Create `.agent/ralph-workflow.toml`:

```toml
[general]
verbosity = 1
```

### `[general.workflow]`

| Key | Default | Description |
|-----|---------|-------------|
| `checkpoint_enabled` | `true` | Enable checkpoint/resume support |
| `unsafe_mode` | `false` | Merge Ralph Workflow MCP into the agent's existing MCP config instead of overwriting it. When `true`, agent-native MCP servers (Claude `~/.claude.json`, OpenCode `opencode.json`, Codex `~/.codex/config.toml`, AGY `.agents/mcp_config.json`, Nanocoder `NANOCODER_MCPSERVERS` and `~/.config/nanocoder/.mcp.json`) are preserved alongside the Ralph Workflow entry. Mirrors the `--unsafe-mode` CLI flag. |

### `[prompt_helper]`

Configuration for the interactive prompt-refinement helper launched by `ralph --prompt-helper` or `ralph-prompt`.

| Key | Default | Description |
|-----|---------|-------------|
| `agent` | _(none)_ | Agent name to use for the prompt-helper session. Omitting this setting causes Ralph Workflow to use the first configured agent in `[agents.*]`. If no agents are configured at all, Ralph Workflow falls back to the built-in `opencode` agent. An explicitly named but unavailable agent raises an error instead of silently falling back. |

Example:

```toml
[prompt_helper]
agent = "claude"
```

The helper does not expose drain configuration, fallback chains, or agent chains — it uses a single interactive agent with an internal standalone session only. See the [CLI Reference](cli.md) for usage.

## Agent chains and drains

Most operator customization happens in `[agent_chains]` and `[agent_drains]` inside `ralph-workflow.toml`.

```toml
[general]
max_retries = 3
retry_delay_ms = 1000

[agent_chains]
planning = ["claude/opus"]
development = ["agy", "opencode/minimax/MiniMax-M2.7-highspeed", "codex", "claude/sonnet"]
analysis = ["opencode/openai/gpt-5.4"]
commit = ["claude/haiku"]

[agent_drains]
planning = "planning"
planning_analysis = "analysis"
development = "development"
development_analysis = "analysis"
development_commit = "commit"
```

`agy` (Google Anti Gravity), `nanocoder`, and `pi` (pi.dev) are also valid agent names in any chain alongside `claude`, `codex`, and `opencode`.

In practice:

- **chains** define fallback order for one kind of work
- **drains** map workflow steps to those chains

Multiple drains can point at the same chain. That lets you change agent policy without rewriting the workflow itself.

### Example: switch development to a different fallback order

```toml
[agent_chains]
development = ["agy", "codex", "claude/sonnet"]
```

Use this when your main question is **"which coding agent should Ralph Workflow try first during implementation?"** — valid agent names include `claude`, `codex`, `opencode`, `nanocoder`, `agy`, and `pi`.

Nanocoder also supports provider/model routing through the same direct-agent syntax used for OpenCode. For example, `nanocoder/ollama/llama3.1` resolves to a built-in Nanocoder invocation with `--provider ollama --model llama3.1`.

### Example: use a repo-local override for one project only

If one repo needs a stricter or more expensive development chain than your default setup, put only the override in `.agent/ralph-workflow.toml`:

```toml
[agent_chains]
development = ["claude/opus", "codex"]
```

That repo-local file overrides the global chain just for that repo.

## User stories: what to edit for common goals

### I want Ralph Workflow to use different coding agents

Edit `ralph-workflow.toml` → `[agent_chains]`.

### I want one repo to behave differently from my defaults

Create or edit `.agent/ralph-workflow.toml`.

### I want to change the workflow shape itself

Edit `.agent/pipeline.toml`, not `ralph-workflow.toml`.

Then read [Advanced Pipeline Configuration](advanced-pipeline-configuration.md).

### I want to enable or customize MCP / web tools

Edit `ralph-workflow-mcp.toml` or `.agent/mcp.toml`.

Then read [Advanced MCP Configuration](advanced-mcp-configuration.md).

### I want to change artifact contracts, decision vocabularies, or summary file outputs

Edit `.agent/artifacts.toml`.

Then read [Advanced Artifact Configuration](advanced-artifact-configuration.md).

### I want to understand what my policy now does after editing it

Run:

```bash
ralph --check-policy
ralph --explain-policy
```

### I broke my config and want to get back to a known-good baseline

Run:

```bash
ralph --regenerate-config
```

Ralph Workflow backs up overwritten files with a `.bak` suffix.

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

### Per-phase display style (`display_style`)

Each phase can declare a `display_style` override in `pipeline.toml` to control the color of its phase banner:

```toml
[phases.planning]
display_style = "theme.phase.planning"
```

When set, this style string is used instead of the role-based default. Without `display_style`, phases inherit a color from their role — for example both `planning` and `development` share `role='execution'` and would otherwise render identically. Set `display_style` to give each phase a visually distinct banner. Available theme keys include `theme.phase.planning`, `theme.phase.development`, `theme.phase.development_analysis`, `theme.phase.commit`, and others defined in `ralph.display.theme`.

## Advanced sections you may not need right away

The main config also supports deeper transport-specific and workflow-authoring sections such as:

- `[ccs]` / `[ccs_aliases]` for explicitly-headless Claude Code Switch defaults
- loop counters and budget counters
- review-role bypass routes
- recovery policy tuning
- parallel fan-out controls

Those sections are useful when you are customizing Ralph Workflow deeply, but many operators never need to touch them. Use `claude-headless` or CCS when you explicitly want the documented non-interactive Claude path.

### `[agents.*] subagent_capability`

Each entry under `[agents.<name>]` accepts an optional `subagent_capability` switch that controls whether the agent's native sub-agent / task tooling is used to dispatch parallel work declared in a plan's `work_units` / `parallel_plan` block. When the switch is `true`, the executing agent decides how to fan work out across its own sub-agents; when it is `false`, the same plan runs sequentially. The bundled `ralph-workflow.toml` ships with `[agents.claude] subagent_capability = true` so parallel plans are dispatched to Claude Code's native sub-agents out of the box.

The default value depends on the resolved transport (see `ralph/config/agent_config.py:_resolve_subagent_capability` and the surrounding `model_post_init` method):

| Agent transport | Default `subagent_capability` |
|-----------------|-------------------------------|
| `claude` | `true` |
| `claude_interactive` | `true` |
| `codex` | `None` — agent decides at runtime |
| `opencode` | `None` — agent decides at runtime |
| `nanocoder` | `None` — agent decides at runtime |
| `agy` | `None` — agent decides at runtime |
| `pi` | `None` — agent decides at runtime |
| `generic` | `None` — agent decides at runtime |

The override precedence is the same as every other Ralph Workflow setting: **CLI flags > project-local `.agent/ralph-workflow.toml` > user-global `~/.config/ralph-workflow.toml` > bundled defaults** (see the precedence list at the top of this page). Set the switch explicitly when you want to override the transport-inferred default — for example, to force a Claude Code run to be sequential without changing every other Claude setting:

```toml
[agents.claude]
subagent_capability = false
```

This is the documented escape hatch: Ralph-managed fan-out stays dormant in this build, and the bundled default never falls back to it automatically. See [Parallel Mode](parallel-mode.md) for the full agent-driven parallelism model and [Advanced Pipeline Configuration](advanced-pipeline-configuration.md) for the `[phases.<name>.parallelization].dispatch_mode` override that pins the dispatch policy at the phase level.

The dormant-fanout audit (`ralph.testing.audit_parallelization_dormant`) pins the literal strings in this section, so a future cleanup cannot silently remove them without breaking `make verify`.

## When to read further

Use the more detailed docs when you need them:

- [Concepts](concepts.md) — terms like phase, drain, and artifact
- [CLI Reference](cli.md) — runtime flags and shortcuts
- [Policy Explanation](policy-explanation.md) — inspect the active workflow in plain English
- [Advanced Pipeline Configuration](advanced-pipeline-configuration.md) — phases, routing, counters, recovery, and fan-out
- [Advanced Artifact Configuration](advanced-artifact-configuration.md) — artifact contracts, decision vocabularies, and summaries
- [Advanced MCP Configuration](advanced-mcp-configuration.md) — MCP servers, search, crawl, and web tooling
- [Developer Reference](developer-reference.md) — implementation-oriented detail
- [End-User Stories](user-stories.md) — common user goals and the shortest docs path for each one
If you want the advanced/operator version of this topic — phases, counters, commit policy, recovery, and parallel fan-out — use [Advanced Pipeline Configuration](advanced-pipeline-configuration.md).

## `artifacts.toml` in plain language

`artifacts.toml` defines the typed outputs Ralph Workflow expects from each drain.

The top-level ideas are:

- `drain` — which phase/drain this artifact belongs to
- `artifact_type` — the structured output kind for the MCP artifact system
- `decision_vocabulary` — allowed analysis decision strings for decision-style artifacts
- `prompt_template` — which prompt template is responsible for producing that artifact
- `markdown_summary_path` — optional human-readable summary path Ralph Workflow writes
- `artifact_json_path` — optional explicit JSON artifact path

In practice, you edit `artifacts.toml` when you want to:

- add or rename workflow artifacts
- change which decisions an analysis artifact may emit
- change where human-readable summaries are written
- add or customize commit-message artifact behavior

If that is your goal, use [Advanced Artifact Configuration](advanced-artifact-configuration.md).

## `mcp.toml` in plain language

`mcp.toml` configures external tool servers and web-capability integrations.

You edit it when you want to:

- add an MCP server over `stdio` or `http`
- configure web search backends
- configure web-visit / readable-page fetching
- wire in advanced crawling like Crawl4AI

If that is your goal, use [Advanced MCP Configuration](advanced-mcp-configuration.md).
