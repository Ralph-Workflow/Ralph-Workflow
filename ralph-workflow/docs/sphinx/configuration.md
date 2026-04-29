# Configuration Reference

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Ralph Workflow uses a layered configuration system. Settings are resolved in this order
(highest priority first):

1. **CLI flags** — override everything
2. **Project-local config** — `.agent/ralph-workflow.toml` in the workspace root
3. **User-global config** — `~/.config/ralph-workflow.toml`
4. **Bundled defaults** — shipped inside the package at `ralph/policy/defaults/`

## Config Files

Ralph Workflow manages a standard first-run config set across two scopes.

### User-Global (created once, shared across projects)

| File | Purpose |
|------|---------|
| `~/.config/ralph-workflow.toml` | Global defaults: agent selection, iteration counts, verbosity |
| `~/.config/ralph-workflow-mcp.toml` | MCP server definitions shared across all projects |

### Project-Local (created per project in `.agent/`)

| File | Purpose |
|------|---------|
| `.agent/ralph-workflow.toml` | Project-specific overrides for the main config, including agent chains and drain bindings |
| `.agent/mcp.toml` | Project-specific MCP server definitions |
| `.agent/pipeline.toml` | Phase sequence and parallel execution settings |
| `.agent/artifacts.toml` | Artifact type schemas and contracts |

Run `ralph --init` to create these standard files from the bundled templates.

## Bundled Default Templates

The bundled defaults live in `ralph/policy/defaults/`. Each file contains inline comments
explaining every field. The canonical reference is the file itself:

- `ralph-workflow.toml` — general config (iterations, review depth, verbosity, isolation)
- `mcp.toml` — empty MCP server list (add custom servers here)
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
| `max_retries` | `3` | Global max retries per agent attempt applied when Ralph Workflow synthesizes chain policy from the main config |
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

Optional per-alias CCS overrides. Ralph Workflow can already resolve `ccs/<alias>` dynamically using the global `[ccs]` defaults, so this section is only needed when a specific alias should behave differently.

Supports two forms:

- **Simple string form** — override the command for a specific alias: `ccs_aliases = { work = "ccs work", personal = "ccs personal" }`
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

## Agent chains and drains in `ralph-workflow.toml`

Normal Ralph Workflow setups define chain order in `[agent_chains]` and drain bindings in
`[agent_drains]` inside `ralph-workflow.toml`.

```toml
[general]
max_retries = 3
retry_delay_ms = 1000

[agent_chains]
planning = ["claude/opus"]
development = ["opencode/minimax/MiniMax-M2.7-highspeed", "codex", "claude/sonnet"]
analysis = ["opencode/openai/gpt-5.4"]
review = ["opencode/openai/gpt-5.4"]
fix = ["opencode/zai-coding-plan/glm-5", "claude/sonnet"]
commit = ["claude/haiku"]

[agent_drains]
planning = "planning"
development = "development"
development_analysis = "analysis"
development_commit = "commit"
review = "review"
review_analysis = "analysis"
review_commit = "commit"
fix = "fix"
```

Ralph Workflow tries agents in order; if one exhausts its retry budget, it falls over to
the next. OpenCode model-qualified identifiers use `opencode/<provider>/<model>` syntax,
for example `opencode/minimax/MiniMax-M2.7-highspeed` or `opencode/zai-coding-plan/glm-5`.
Claude model tags are shorter: `claude` uses your current Claude Code model/profile, while
`claude/opus` and `claude/sonnet` force those model families for a specific chain entry.

`[agent_drains]` maps each pipeline drain name (matching a phase's `drain` field in
`pipeline.toml`) to a chain name from `[agent_chains]`. Multiple drains may share one
chain — for example, `development_analysis` and `review_analysis` both use the `analysis`
chain by default. The built-in runtime drain names are: `planning`, `development`,
`development_analysis`, `development_commit`, `review`, `review_analysis`,
`review_commit`, and `fix`.

## `pipeline.toml` Policy Fields

The `pipeline.toml` file declares all workflow behavior. Ralph Workflow validates the
complete policy at startup and rejects incomplete configurations with actionable errors.

### Top-level fields

| Field | Description |
|-------|-------------|
| `entry_phase` | Phase where every run starts |
| `terminal_phase` | Phase that marks successful completion |

### `[phases.<name>]`

Declares a pipeline phase. Required and optional fields:

| Field | Required? | Description |
|-------|-----------|-------------|
| `role` | Yes | Phase behavior class: `execution`, `analysis`, `review`, `commit`, `verification`, `terminal`, `fanout_join` |
| `drain` | Yes (unless terminal) | Agent chain binding name |
| `prompt_template` | No | Jinja prompt template filename |
| `skip_invocation` | No | When true, the phase routes without invoking an agent |
| `terminal_outcome` | Required for `terminal` | `"success"` or `"failure"` |

#### `[phases.<name>.transitions]`

| Field | Description |
|-------|-------------|
| `on_success` | Phase to advance to on agent success |
| `on_failure` | Phase to advance to on agent failure |
| `on_loopback` | Phase to loop back to |

#### `[phases.<name>.loop_policy]` (analysis-role phases)

| Field | Description |
|-------|-------------|
| `max_iterations` | Maximum loop iterations before treating next outcome as failure |
| `iteration_state_field` | Names the loop counter declared in `[loop_counters.*]` |
| `loopback_review_outcome` | (Optional) Review outcome keyword that triggers loopback |

#### `[phases.<name>.decisions.<key>]` (analysis-role phases)

Maps a decision vocabulary key to a routing target:

| Field | Description |
|-------|-------------|
| `target` | Phase to route to when this decision is emitted |
| `reset_loop` | Whether to reset the loop counter when this decision is taken |

#### `[phases.<name>.commit_policy]` (commit-role phases)

| Field | Description |
|-------|-------------|
| `requires_artifact` | Whether a commit message artifact is required |
| `skipped_advances_progress` | Whether a skipped commit still increments budget |
| `increments_counter` | Names the budget counter declared in `[budget_counters.*]` |
| `loop_resets` | List of loop counter names to reset on commit |

#### `[phases.<name>.bypass_routes]` (review-role phases)

Named outcome bypasses. Example: `review_clean = "review_commit"` bypasses the
analysis phase when the review finds no issues.

### `[loop_counters.<name>]`

Declares a loop iteration counter referenced by `loop_policy.iteration_state_field`.

| Field | Description |
|-------|-------------|
| `default_max` | Default cap (can be overridden by `--developer-iters` / `--reviewer-reviews`) |
| `description` | Human-readable description |

### `[budget_counters.<name>]`

Declares a budget counter referenced by `commit_policy.increments_counter`.

| Field | Description |
|-------|-------------|
| `description` | Human-readable description |
| `tracks_budget` | When true, participates in post-commit routing (remaining / exhausted / no_review) |

### `[[post_commit_routes]]`

Declares post-commit routing based on the phase that committed and the resulting
budget state:

```toml
[[post_commit_routes]]
target = "review"
[post_commit_routes.when]
phase = "development_commit"
budget_state = "exhausted"  # remaining | exhausted | no_review
```

### `[parallel_execution]`

| Field | Description |
|-------|-------------|
| `source` | Source of work units (`"planning_artifact_work_units"`) |
| `phase` | Phase eligible for parallel fan-out |
| `max_parallel_workers` | Maximum concurrent workers |
| `max_work_units` | Maximum work units in a plan |
| `require_allowed_directories` | Whether workers must declare edit-area fencing |
| `post_fanout_verification` | Whether a verification phase runs after fan-out completes |

### `[default_phase_retry_policy]`

Applied to all phases without an explicit `retry_policy` block:

| Field | Description |
|-------|-------------|
| `max_retries` | Maximum retries per agent attempt |
| `retry_delay_ms` | Base delay between retries |
| `retry_in_session` | Whether retry preserves the agent session |

### `[recovery]`

| Field | Description |
|-------|-------------|
| `cycle_cap` | Maximum full recovery cycles before terminal failure |
| `terminal_recovery_route` | Where terminal failures route: `"failed"`, `"exit_failure"`, or a declared phase name |
| `preserve_session_on_categories` | Failure categories that allow session-preserving retry |

## Inspecting the active policy

After editing `pipeline.toml`, confirm the workflow is complete and valid:

```bash
ralph --check-config       # validate configuration
ralph --explain-policy     # print a human-readable policy summary
```

See [Policy Explanation](policy-explanation.md) for a walkthrough of the explain output.

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

Edit `.agent/ralph-workflow.toml`. Set the relevant `[agent_chains]` entry to just
`["your-agent"]`, and update `[agent_drains]` only if you need custom drain-to-chain routing.

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
- [Concepts](concepts.md) — pipeline phases, drains, and agent terminology
- [Policy Explanation](policy-explanation.md) — `ralph --explain-policy` walkthrough
- [Policy-Driven Migration](policy-driven-overhaul-migration.md) — upgrading from earlier versions
