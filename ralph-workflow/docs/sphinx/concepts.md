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

A named step in the pipeline sequence declared in `.agent/pipeline.toml`.
Every phase has a **role** that determines what the runtime expects from it and
how it routes on completion. The bundled defaults define phases named
`planning`, `development`, `development_analysis`, `development_commit`,
`review`, `review_analysis`, `fix`, `review_commit`, and `complete`, but these
are examples — any phase name is valid.

Phase roles:

| Role | Meaning |
|------|---------|
| `execution` | Invokes an agent and routes on `on_success` / `on_failure` |
| `analysis` | Reads an artifact decision and routes via `decisions` map |
| `review` | Invokes an agent; routes on `review_clean` bypass or `on_success` |
| `commit` | Invokes the commit agent; tracks budget via `commit_policy` |
| `verification` | Invokes an agent; gates advancement on artifact evidence |
| `terminal` | Ends the pipeline with a declared `terminal_outcome` |
| `fanout_join` | Aggregates parallel worker results |

See `ralph.policy.models.PhaseRole` and `ralph.policy.validation` for the role contracts.
See `ralph.phases` for phase handlers and `ralph.pipeline` for orchestration.

## Drain

A named binding that maps a phase to an agent chain. Each phase that invokes an agent
has a drain; the drain name resolves to a chain via the main Ralph Workflow config (`ralph-workflow.toml`).
For example, the `development` phase uses the `development` drain, which resolves to the
configured developer agent chain. See `ralph.policy.models` for the drain/chain data model.

## Agent

An external AI coding assistant invoked as a subprocess (e.g., `claude`, `opencode`).
Agents are configured in the main Ralph Workflow config with a `cmd` field (the binary name), optional
model flags, and optional MCP server bindings. Ralph Workflow does not require a specific
agent; any tool that reads stdin/args and writes to stdout works. See `ralph.agents`.

## Agent Chain

An ordered list of agents tried in sequence. If the first agent fails or produces
unusable output, Ralph Workflow falls over to the next agent in the chain (agent
fallover). Chains are declared in `ralph-workflow.toml` under `[agent_chains]`. See
`ralph.agents.chain`.

## Agent Fallover

When an agent in a chain fails (non-zero exit, timeout, or artifact parse error),
Ralph Workflow retries with the next agent in the chain up to `max_retries` times.
Fallover is transparent to the phase logic. See `ralph.agents.chain`.

## Agent Execution

### ExecutionStrategy seam

Each agent transport has an `ExecutionStrategy` that encapsulates how Ralph Workflow interprets
that agent's lifecycle signals. The strategy is resolved by `strategy_for_transport()`
in `ralph.agents.execution_state` and is the only place that maps an `AgentTransport`
to lifecycle semantics.

**GenericExecutionStrategy** is the default for Claude, Codex, and unknown transports.
It treats a clean process exit (exit code 0) as terminal success regardless of artifact
presence. It uses OS-level descendant checks for liveness but does not consult the
`LivenessProbe`.

**OpenCodeExecutionStrategy** is used for the OpenCode transport. It requires explicit
completion signals — either a required artifact on disk or a `declare_complete` MCP tool
call — before declaring a run terminal. A clean exit without either signal raises
`OpenCodeResumableExitError`, which the runner maps to a session-preserving retry
(threading the existing `session_id` into the next attempt instead of restarting from
scratch). The strategy also consults the `LivenessProbe` to detect active child agents
before declaring the parent idle; this prevents misclassifying an OpenCode parent as
timed-out while child work is still running.

New agent transports that need resumable or child-delegating semantics should extend
this seam by implementing a new strategy and registering it in `strategy_for_transport()`,
rather than adding special cases in the generic timeout or idle logic.

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

## Local Web Access

Ralph Workflow exposes three distinct web capabilities — search, visit, and crawl — that are
designed to be complementary. Web search finds candidate pages, `visit_url` reads a
single page directly, and Crawl4AI/Firecrawl can be configured as an upstream local
crawler for multi-page traversal. See [Local Web Access](local-web-access.md) for the
full product surface, SSRF safety posture, and phase visibility guarantees.

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

## Work Unit

A discrete, independently executable sub-task declared in a planning artifact's
`work_units` list. When the planning agent identifies that tasks can be parallelized,
it emits multiple work units with distinct `unit_id` values. Ralph Workflow then runs
each work unit concurrently using its parallel executor, with each unit getting its own
MCP session and coordination context. Work units are validated against the parallel
policy (`max_work_units`, `max_parallel_workers`) before execution begins. See
`ralph.pipeline.work_units` and [Parallel Work Units](#parallel-work-units).

## Isolation Mode

Parallel workers share the same git checkout (same-workspace mode). Isolation is
enforced at the path level: each work unit must declare an `allowed_directories` list,
and the workspace scope for that worker is write-fenced to those directories. Workers
cannot write outside their declared edit areas. Reserved paths (`.agent`, `.git`, `.`)
may never be declared as edit areas.

Single-agent (non-parallel) invocations use the standard workspace scope and are not subject to per-worker fencing. See `ralph.pipeline.parallel` for the same-workspace
coordinator and `ralph.pipeline.work_units` for the validation logic.

(parallel-work-units)=
## Parallel Work Units

When the planning artifact declares multiple `work_units`, Ralph Workflow runs them
concurrently using the parallel executor. Each work unit gets its own MCP session and
coordination context. Parallel execution requires `[phases.<phase>.parallelization]`
to be declared in `pipeline.toml`. See `ralph.pipeline.parallel` and `ralph.mcp.tools`
(coordinate tool).

See also: [Parallel Mode](parallel-mode.md) for a detailed walkthrough.

## Ambiguous Failure Category

A failure classification used by the recovery classifier when the cause of a phase
failure cannot be clearly attributed to a specific category (agent error, environment
issue, policy violation, etc.). When a failure is classified as `ambiguous`,
Ralph Workflow retries without counting against the budget debit — the retry proceeds
but the failure is flagged for review. See `ralph.recovery.classifier.FailureCategory`
and `ralph.recovery.controller`.

## Connectivity Monitor

A background component that periodically checks whether the host machine has outbound
network connectivity. When connectivity is lost during a pipeline run, the monitor
signals the runner to pause or abort gracefully rather than let agent invocations time
out silently. In tests, a `FakeConnectivityMonitor` is used. See
`ralph.recovery.connectivity.ConnectivityMonitor` and `ralph.recovery`.

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

## Loop Counter

A policy-declared counter that tracks how many times an analysis loop has iterated.
Loop counters are declared in `pipeline.toml` under `[loop_counters.<name>]` with a
`default_max` that caps the loop. Each analysis-role phase references a loop counter
via `loop_policy.iteration_state_field`. When the counter reaches its cap, the
pipeline treats the next analysis outcome as a failure rather than a loopback.

The `--developer-iters` and `--reviewer-reviews` CLI flags override the `default_max`
of the named loop counters at runtime.

See `ralph.policy.models.LoopCounterConfig`.

## Budget Counter

A policy-declared counter that tracks outer progress across commit-role phases.
Budget counters are declared under `[budget_counters.<name>]`. Each commit-role
phase's `commit_policy.increments_counter` names which budget counter to increment
when a commit completes. Counters with `tracks_budget = true` participate in
post-commit routing decisions (remaining, exhausted, no_review states). See
`ralph.policy.models.BudgetCounterConfig`.

Post-commit routing is declared in `[[post_commit_routes]]` entries in `pipeline.toml`,
matched by `when.phase` (the commit phase name) and `when.budget_state` (remaining,
exhausted, or no_review). Counter names are fully policy-declared — the runtime has no
built-in knowledge of `iteration` or `reviewer_pass`; any name works. Policy validation
enforces that every commit-role phase whose counter has `tracks_budget = true` declares
at least one matching `post_commit_routes` entry, so incomplete configurations are
rejected at startup rather than silently routing on `on_success`. See
`ralph.policy.validation._validate_post_commit_routes_coverage`.

## Recovery Policy

The `[recovery]` block in `pipeline.toml` governs pipeline-wide failure behavior.
Key fields:

| Field | Meaning |
|-------|---------|
| `cycle_cap` | Maximum number of full recovery cycles before terminal failure |
| `terminal_recovery_route` | Where terminal failures route: `"failed"`, `"exit_failure"`, or a declared phase name |
| `preserve_session_on_categories` | Which failure categories allow session-preserving retry |

See `ralph.policy.models.RecoveryPolicy`.

## Policy Explanation

The `ralph --explain-policy` command renders the active policy as a human-readable
summary. It lists all phases (with roles and drains), loop counters, budget counters,
terminal outcomes, parallel execution settings, and recovery routing. This is useful
for confirming a configuration before running and for documenting the workflow your
project uses.

See [Policy Explanation](policy-explanation.md) for the full walkthrough.

## Related pages

- [Getting Started](getting-started.md) — first-run walkthrough with phases explained
- [Configuration](configuration.md) — agents, drains, and pipeline config
- [Policy Explanation](policy-explanation.md) — `ralph --explain-policy` walkthrough
- [Policy-Driven Migration](policy-driven-overhaul-migration.md) — upgrading from earlier versions
- [Recovery](recovery.md) — retry behavior, cycles, and checkpoints
- [Parallel Mode](parallel-mode.md) — work units and concurrent execution
