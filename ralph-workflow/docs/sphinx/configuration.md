# Configuration Reference

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Ralph Workflow uses a layered configuration system. Settings are resolved in this order
(highest priority first):

1. **CLI flags** — override everything
2. **Project-local config** — `.agent/ralph-workflow.toml` in the workspace root
3. **User-global config** — `~/.config/ralph-workflow.toml`
4. **Bundled defaults** — shipped inside the package at `ralph/policy/defaults/`

## Config Files

Ralph Workflow manages seven config files across two scopes.

### User-Global (created once, shared across projects)

| File | Purpose |
|------|---------|
| `~/.config/ralph-workflow.toml` | Global defaults: agent selection, iteration counts, verbosity |
| `~/.config/ralph-workflow-mcp.toml` | MCP server definitions shared across all projects |

### Project-Local (created per project in `.agent/`)

| File | Purpose |
|------|---------|
| `.agent/ralph-workflow.toml` | Project-specific overrides for the main config |
| `.agent/mcp.toml` | Project-specific MCP server definitions |
| `.agent/agents.toml` | Agent chain policies and drain bindings |
| `.agent/pipeline.toml` | Phase sequence and parallel execution settings |
| `.agent/artifacts.toml` | Artifact type schemas and contracts |

Run `ralph --init` to create all of these from the bundled templates.

## Bundled Default Templates

The bundled defaults live in `ralph/policy/defaults/`. Each file contains inline comments
explaining every field. The canonical reference is the file itself:

- `ralph-workflow.toml` — general config (iterations, review depth, verbosity, isolation)
- `mcp.toml` — empty MCP server list (add custom servers here)
- `agents.toml` — default agent chain policies (`claude`, `opencode`), chains, and drains
- `pipeline.toml` — default phase sequence and parallel execution policy
- `artifacts.toml` — artifact type contracts

## `ralph-workflow.toml` Sections

The main config file (`~/.config/ralph-workflow.toml` and `.agent/ralph-workflow.toml`)
is organized into the following sections. All fields are optional; commented-out fields
use their documented defaults automatically.

### `[general]`

Core workflow settings: iteration counts, verbosity, review depth, git identity, and
retry behavior.

| Key | Default | Description |
|-----|---------|-------------|
| `verbosity` | `2` | Output verbosity: 0=quiet, 1=normal, 2=verbose, 3=full, 4=debug |
| `developer_iters` | `5` | Developer agent iterations per run |
| `reviewer_reviews` | `2` | Review-fix cycles (0 = skip review) |
| `review_depth` | `"standard"` | `standard`, `comprehensive`, `security`, or `incremental` |
| `git_user_name` | (from git config) | Git author name for commits |
| `git_user_email` | (from git config) | Git author email for commits |
| `max_retries` | `3` | Global max retries per agent attempt (overridden by per-chain settings in `agents.toml`) |
| `retry_delay_ms` | `1000` | Base delay between retries in ms |
| `backoff_multiplier` | `2.0` | Exponential backoff multiplier |
| `max_backoff_ms` | `60000` | Maximum retry backoff delay in ms |
| `max_cycles` | `3` | Max full fallback cycles through a drain before giving up |
| `agent_idle_timeout_seconds` | `300.0` | Max idle seconds before killing a stalled agent |

### `[general.behavior]`

Behavioral flags that control optional runtime features.

| Key | Default | Description |
|-----|---------|-------------|
| `interactive` | `false` | Keep agent in foreground (interactive mode) |
| `auto_detect_stack` | `true` | Auto-detect project stack for review guidelines |
| `strict_validation` | `false` | Strict PROMPT.md validation |

### `[general.workflow]`

Workflow automation flags.

| Key | Default | Description |
|-----|---------|-------------|
| `checkpoint_enabled` | `true` | Enable checkpoint/resume functionality |

### `[general.execution]`

Execution behavior flags.

| Key | Default | Description |
|-----|---------|-------------|
| `force_universal_prompt` | `false` | Force universal review prompt for all agents |

### `[ccs]`

Claude Code Switch (CCS) defaults — controls how Ralph Workflow invokes Claude Code
agent binaries. These fields mirror the flags that `claude` accepts.

| Key | Description |
|-----|-------------|
| `output_flag` | Flag to request JSON streaming output |
| `yolo_flag` | Flag to set permission mode |
| `verbose_flag` | Flag for verbose agent output |
| `print_flag` | Flag for print/non-interactive mode |
| `streaming_flag` | Flag to include partial messages |
| `json_parser` | Parser to use: `"claude"` or `"opencode"` |
| `session_flag` | Flag template for session resume |
| `can_commit` | Whether this agent type is allowed to make commits |

### `[ccs_aliases]`

Maps named agent identifiers to their CCS settings. Supports two forms:

- **Simple string form** — use a built-in named alias: `ccs_aliases = { claude = "claude", opencode = "opencode" }`
- **Table form** — override per-alias CCS settings individually:

```toml
[ccs_aliases.claude]
cmd = "claude"
model_flag = "--model claude-sonnet-4"
can_commit = true
```

### `[agents.*]`

Per-agent definitions. Each `[agents.<name>]` block defines a named agent with its
invocation command and flags. Example:

```toml
[agents.claude]
cmd = "claude"
output_flag = "--output-format=stream-json"
yolo_flag = "--permission-mode auto"
can_commit = true
display_name = "Claude"
```

The canonical field list is in `ralph/policy/defaults/ralph-workflow.toml`.

### `[cloud]`

Optional cloud reporting integration. Leave disabled unless you have an account.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable cloud reporting |
| `api_url` | — | Base URL for cloud API |
| `api_key` | `""` | API key (prefer `RALPH_CLOUD_API_KEY` env var) |
| `timeout_secs` | `30` | Request timeout |

## `.agent/agents.toml` Sections

The agent policy file (`.agent/agents.toml` project-local, or the bundled default at
`ralph/policy/defaults/agents.toml`) uses an **expanded block format** that is distinct
from the simple override shortcuts available in `ralph-workflow.toml`. This file controls
which agents are tried for each pipeline drain and how retries behave per chain.

### `[agent_chains.<name>]`

Named reusable chain definitions. Each block lists an ordered fallback sequence of agent
names, with per-chain retry settings:

```toml
[agent_chains.development]
agents = ["claude", "opencode"]
max_retries = 3          # per-agent retry budget in this chain
retry_delay_ms = 1000    # base delay before retry (exponential backoff)

[agent_chains.review]
agents = ["claude"]
max_retries = 3
retry_delay_ms = 1000
```

Ralph Workflow tries agents in order; if one exhausts its `max_retries` budget, it falls
over to the next. `retry_delay_ms` controls the base delay with exponential backoff
(capped at 30 s).

### `[agent_drains.<name>]`

Drain-to-chain bindings for built-in pipeline drains. Maps each phase drain name to a
chain defined in `[agent_chains.*]`:

```toml
[agent_drains.planning]
chain = "planning"

[agent_drains.development]
chain = "development"

[agent_drains.review]
chain = "review"

[agent_drains.fix]
chain = "fix"
```

The built-in drain names are: `planning`, `development`, `development_analysis`,
`development_commit`, `review`, `review_analysis`, `review_commit`, `fix`, `commit`.
See `ralph.policy.models` for the full drain/chain model.

## Regenerating Configs

```bash
ralph --regenerate-config
```

Rewrites all configs from the bundled templates. Existing files are backed up with a
`.bak` suffix before being overwritten, so no data is lost.

## Frequently Asked Questions

### I have no agents installed

Ralph Workflow will start but will fail when it tries to invoke an agent. Install at
least one supported agent:

- **Claude Code**: see <https://docs.claude.com/claude-code>
- **opencode**: see <https://opencode.ai>

Then verify with `ralph --diagnose`.

### I want to use a single agent only

Edit `.agent/agents.toml`. Find the `[agent_chains.<name>]` block for the relevant chain
and set `agents` to just `["your-agent"]`. Remove any fallover entries you do not need.

### How do I add a custom MCP server

Add a `[[servers]]` entry to `.agent/mcp.toml`:

```toml
[[servers]]
name = "my-server"
command = ["npx", "my-mcp-server"]
```

Validate with `ralph --check-mcp` after editing.

## Related pages

- [Getting Started](getting-started.md) — first-run walkthrough
- [CLI Reference](cli.md) — all flags and sub-commands
- [Agents](agents.md) — agent chain definitions and transport details
- [Concepts](concepts.md) — pipeline phases, drains, and agent terminology
