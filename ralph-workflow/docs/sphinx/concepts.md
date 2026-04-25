# Concepts

> **New to Ralph Workflow?** See [Getting Started](getting-started.md) before reading this page — it introduces the workflow before the terminology.

Key terms and mental models used throughout Ralph Workflow.

## PROMPT.md

The task-description file that Ralph Workflow reads as its only input. It lives in the
workspace root and contains a `# Goal` section and an `## Acceptance criteria` section
at minimum. Ralph Workflow validates that PROMPT.md exists, is non-empty, and has had
the starter sentinel removed before allowing the pipeline to run. See
`ralph.policy.validation` for the validation logic.

## Phase

A named step in the pipeline sequence. The default pipeline has phases:
`planning`, `development`, `development_analysis`, `development_commit`,
`review`, `review_analysis`, `review_commit`, `fix`, and `complete`.
Phases are declared in `.agent/pipeline.toml`; the order there defines execution order.
See `ralph.phases` for phase resolution and `ralph.pipeline` for orchestration.

## Drain

A named binding that maps a phase to an agent chain. Each phase that invokes an agent
has a drain; the drain name resolves to a chain via `agents.toml`. For example,
the `development` phase uses the `development` drain, which resolves to the configured
developer agent chain. See `ralph.policy.models` for the drain/chain data model.

## Agent

An external AI coding assistant invoked as a subprocess (e.g., `claude`, `opencode`).
Agents are configured in `agents.toml` with a `cmd` field (the binary name), optional
model flags, and optional MCP server bindings. Ralph Workflow does not require a specific
agent; any tool that reads stdin/args and writes to stdout works. See `ralph.agents`.

## Agent Chain

An ordered list of agents tried in sequence. If the first agent fails or produces
unusable output, Ralph Workflow falls over to the next agent in the chain (agent
fallover). Chains are declared in `agents.toml` under `[agent_chains]`. See
`ralph.agents.chain`.

## Agent Fallover

When an agent in a chain fails (non-zero exit, timeout, or artifact parse error),
Ralph Workflow retries with the next agent in the chain up to `max_retries` times.
Fallover is transparent to the phase logic. See `ralph.agents.chain`.

## MCP (Model Context Protocol)

A protocol that lets Ralph Workflow expose tools and resources to agents over a local
server. Ralph Workflow runs an MCP server during each agent invocation; agents connect
to it to read files, submit structured artifacts, coordinate parallel work, and report
progress. See `ralph.mcp` and `ralph.mcp.server`.

## MCP Artifact

A structured JSON payload submitted by an agent through the MCP `submit_artifact` tool.
Artifact types include: `plan`, `development_result`, `issues`, `fix_result`,
`commit_message`, `development_analysis_decision`, and `review_analysis_decision`.
Each type has a schema; invalid artifacts are rejected. See `ralph.mcp.artifacts`.

## Checkpoint

A snapshot of pipeline state saved after each phase completes. If Ralph Workflow is
interrupted, the next run detects the checkpoint and resumes from the last completed
phase. Use `ralph --inspect-checkpoint` to display the current checkpoint and
`ralph --no-resume` to ignore it and restart from the beginning. See `ralph.checkpoint`.

## Recovery Cycle

When a development phase produces output that does not satisfy the acceptance criteria,
the review phase classifies the issues and triggers a fix phase. This review → fix loop
repeats up to `--reviewer-reviews` times. The recovery controller decides whether to
continue, escalate, or abort based on issue severity. See `ralph.recovery`.

## Isolation Mode

When enabled (the default), each agent invocation runs in a clean environment with
limited filesystem access scoped to the workspace. Disable with `--no-isolation` for
debugging. See `ralph.policy.models` for the isolation config field.

## Parallel Work Units

When the planning artifact declares multiple `work_units`, Ralph Workflow runs them
concurrently using the parallel executor. Each work unit gets its own MCP session and
coordination context. Parallel execution requires `pipeline.parallel_execution` to be
configured. See `ralph.pipeline.parallel` and `ralph.mcp.tools` (coordinate tool).

## Transcript Layout

Every line Ralph Workflow prints has a level, a category (`META` or `CONT`), and a tag
(the specific sub-operation). Verbosity controls which levels are shown.

Levels (least to most severe):

| Level | Meaning |
|-------|---------|
| `INFO` | Routine update or progress |
| `SUCCESS` | Phase or pipeline completed successfully |
| `WARN` | Non-fatal issue or degraded state |
| `ERROR` | Fatal error or malformed input |
| `MILESTONE` | Major phase transition (planning, development, review, fix) |

Categories:

| Category | Meaning |
|----------|---------|
| `META` | Workflow metadata: phase, plan, activity, worker, result, etc. |
| `CONT` | Agent-produced content: text, thinking, tool calls, errors |

Use `--debug` to see all levels or `--quiet` to suppress everything except errors. See
`ralph.display`.

## Verbosity

Controls how much output Ralph Workflow produces. Levels from least to most:
`quiet`, `normal`, `verbose` (default), `full`, `debug`. Pass `--verbosity <level>`,
`--quiet`, or `--debug` on the command line. See `ralph.config.enums.Verbosity`.
