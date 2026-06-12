---
orphan: true
---

# Parallel Mode

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

## What changed

Parallel plan execution is now **delegated to the executing AI agent's native
sub-agent / task tooling** (Claude Code sub-agents, OpenCode task tool, Codex
sub-agents, AGY task tooling, etc.). The bundled `pipeline.toml` ships with
`dispatch_mode = "agent_subagents"` on the development phase, so the executing
agent is the actor that dispatches its own sub-agents and produces the matching
`plan_items_proven` evidence. **Ralph-managed fan-out is dormant in this build**:
the same-workspace fan-out worker machinery is retained in policy for future
re-arming, but the bundled default does not use it for parallel plan execution.
See [Re-arming Ralph-managed fan-out (dormant)](#re-arming-ralph-managed-fan-out-dormant)
below if you need the legacy worker-based flow.

## How plans express parallelization intent

A plan communicates parallelization intent to the executing agent through two
shapes. Both are **agent-facing intent**, not Ralph fan-out instructions:

- `work_units` — same-workspace agent-driven chunks. The planner assigns each
  unit an `allowed_directories` scope; the executing agent dispatches a
  sub-agent per unit, scoped to that unit's directories, and produces the
  matching `plan_items_proven` evidence.
- `parallel_plan` — read-mostly chunks (e.g. parallel exploration,
  investigation, or doc analysis) where the executing agent's sub-agents work
  on disjoint inputs and the planner defines the per-unit scope contract.

A plan with no parallelizable work remains just as expressible as before — omit
both shapes and the executing agent runs the plan sequentially.

## How the executing agent dispatches sub-agents

When a plan declares `work_units` or `parallel_plan`, the executing agent:

1. Reads the `allowed_directories` of each work unit.
2. Dispatches a sub-agent per unit in dependency order.
3. Aggregates each sub-agent's `plan_items_proven` evidence into the
   `development_result` artifact.

For capable agents, the agent's native sub-agent / task capability is enabled
by default via `[agents.<name>] subagent_capability = true` in
`ralph-workflow.toml` (see the [Configuration Reference](configuration.md)
table for the per-agent default). Agents without usable sub-agent capability
(e.g. `nanocoder`) execute the same plan sequentially in `unit_id` order — no
correctness loss.

The planning prompt (`planning.jinja`) carries the new
`## Agent-Driven Parallel Execution` block that tells the planner to write
agent-facing intent (work units, dependencies, scope) and forbids the
Ralph-managed coordination primitives (`ralph coordinate claim` /
`ralph coordinate release`) in the plan instructions. The continuation
template (`developer_iteration_continuation.jinja`) carries the matching
`## PARALLEL EXECUTION` block so non-initial-iteration runs still receive the
sub-agent dispatch guidance.

## Re-arming Ralph-managed fan-out (dormant)

Ralph-managed fan-out is retained in policy for future use. To opt back into
the same-workspace worker model, set the development phase's
`parallelization.dispatch_mode` to `ralph_fan_out` in `pipeline.toml`:

```toml
[phases.development.parallelization]
dispatch_mode = "ralph_fan_out"
mode = "same_workspace"
max_parallel_workers = 4
max_work_units = 50
```

Under `ralph_fan_out` the pipeline falls back to the legacy worker flow.
The same-workspace model means there are no separate per-worker checkouts
and no post-development merge step: workers share the checkout and are
isolated from each other with path restrictions (`allowed_directories`)
and per-worker artifact namespaces. Per-worker state is scoped to
`.agent/workers/<unit_id>/` (artifacts, logs, tmp, handoffs). Per-worker
prompt payloads are written under
`.agent/workers/<unit_id>/tmp/prompt_payloads/` so concurrent workers
cannot overwrite each other's payload files. Workers coordinate through
the `mcp__ralph__coordinate` tool exposed by the MCP server.

The bundled default does not enable this path; the override is explicit
and per-phase. See the [Pipeline Policy](advanced-pipeline-configuration.md)
page for the full `[phases.<name>.parallelization]` reference.

## Related pages

- [Configuration Reference](configuration.md) — `[agents.<name>] subagent_capability` default
- [Pipeline Policy](advanced-pipeline-configuration.md) — `dispatch_mode` override
- [Concepts](concepts.md) — work units and parallel execution terminology
- [Recovery](recovery.md) — recovery behavior and failure classification
