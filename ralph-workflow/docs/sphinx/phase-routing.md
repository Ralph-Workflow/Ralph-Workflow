# Phase routing and run lifecycle

> **Mental model page.** This is explanation, not a how-to. For the practical
> configuration path, see [Configuration](configuration.md) and
> [Advanced pipeline configuration](advanced-pipeline-configuration.md).

A **phase** is one unit of work in the Ralph Workflow pipeline. A **route**
is the decision about which agent handles the phase. A **drain** is the
named terminal condition a phase produces (success, fix-needed, blocked,
and so on). Together they define the run lifecycle.

## What a phase is

A phase has:

- A **name** (e.g. `planning`, `development`, `review`)
- A **route** — which agent handles it (e.g. `claude-headless`)
- A **prompt template** — the Jinja template that materializes the phase's
  prompt from the current state
- An **artifact contract** — what the phase must produce
- A **drain set** — the terminal conditions the phase can return

Phases are declared in policy under `[phases]` and
`[phases.<name>]`. The runtime reads the declaration and binds the route
to an agent via `ralph/agents/registry.py`.

## What a drain is

A **drain** is a named terminal condition that a phase can end in. Examples:

- `done` — phase produced the required artifact
- `fix-needed` — phase produced a partial artifact, downstream phase should
  route to a fix cycle
- `blocked` — phase cannot complete without human intervention
- `retry` — phase hit a transient failure, runtime should re-attempt

Drains are declared in policy under `[phases.<name>.drains]`. The runtime
uses the drain name to decide the next effect. This is the core of phase
routing: a phase ends with a drain, and the next phase (or the recovery
layer) consumes that drain.

## The run lifecycle

A typical Ralph Workflow run looks like:

```text
[planning] --done--> [development] --done--> [verification] --done--> [review]
                              |                          |                |
                          fix-needed                  blocked        approve
                              |                          |                |
                              v                          v                v
                        [development-fix]          [recovery]       [commit]

                          blocked             done
                              |                |
                              v                v
                        [recovery]      [recovery]
```

The shape is declared entirely in policy. The runtime is a state machine
that consults policy at each transition. If a transition is unspecified,
the runtime fails closed with a policy validation error.

## Reducers and effects

The runtime has two complementary structures:

- **Reducers** — pure functions of `(state, event) -> state`. They update
  the `PipelineState` in response to events (artifact submission, agent
  invocation result, watchdog signal).
- **Effects** — imperative actions the runtime performs in response to
  the new state (spawn agent, write checkpoint, request recovery).

The split is intentional: reducers are testable in isolation (no I/O),
effects are the integration points with the filesystem, agent subprocess,
and MCP server. See `ralph/pipeline/reducers/` and
`ralph/pipeline/effects/`.

## Checkpoints

After every reducer the runtime writes a checkpoint to
`.agent/checkpoint.json`. The checkpoint captures:

- The current `PipelineState`
- The drain the previous phase returned
- The artifact path the previous phase produced
- The agent and model the previous phase used
- The prompt template that was materialized

If the run is interrupted, the next `ralph` invocation reads the
checkpoint and resumes from the last completed phase. `ralph --inspect-checkpoint`
prints the current checkpoint in human-readable form.

## Fan-out

For multi-unit plans, the policy can declare `parallel_plan` or
`work_units`. Parallel execution is delegated to the executing AI agent
in the bundled default — Ralph-managed fan-out is dormant and retained
only for future use. See [Parallel mode](parallel-mode.md) for the
opt-in contract.

When Ralph-managed fan-out is enabled, each worker enters through a
dedicated bootstrap path that bypasses the shared pipeline startup loop
(see `ralph/pipeline/parallel/` for the worker entry points).

## Terminal outcomes

A run ends in one of four terminal outcomes:

| Outcome        | Meaning                                                                  |
| -------------- | ------------------------------------------------------------------------ |
| `done`         | Every phase produced its artifact; commit is ready                        |
| `blocked`      | A phase returned `blocked`; human intervention needed                    |
| `budget-exceeded` | The retry budget or session ceiling was hit; current state is recoverable |
| `regression`   | Verification failed after retry; partial result |
The terminal outcome is what the runtime hands back to the user via
`declare_complete`. It is what the user reviews in the morning.

## Related pages

- [Policy-driven pipeline](policy-driven-pipeline.md)
- [Recovery](recovery.md) — the recovery layer that handles drains
- [Artifact lifecycle](artifact-lifecycle.md) — what each phase produces
- [Watchdogs and timeouts](watchdogs-and-timeouts.md) — how the runtime
  detects stuck phases
- [Parallel mode](parallel-mode.md) — multi-unit fan-out