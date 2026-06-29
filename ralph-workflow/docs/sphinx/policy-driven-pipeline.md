# Policy-driven pipeline

> **Mental model page.** This is explanation, not a how-to. For the practical
> configuration path, see [Configuration](configuration.md).

A **policy-driven pipeline** is a runtime that follows a declared policy
and fails closed when the policy is unsatisfied. Ralph Workflow's runtime
takes this shape: every phase, drain, recovery rule, artifact contract,
and terminal condition is **declared in policy**, not hard-coded in
Python.

## Why policy is the right boundary

Two alternatives were possible:

1. **Hard-code the pipeline in Python.** Simple, but every customization
   becomes a code change. The default workflow becomes the only workflow.
2. **Make the user write a config from scratch.** Flexible, but every
   project starts with zero leverage. The first run is a research project.

Ralph Workflow picks a third shape: **the runtime follows declared policy**
and ships a **bundled default policy** that is strong enough to start with.
The user can override any policy section without touching Python.

## What lives in policy

The policy bundle declares everything the runtime needs to know:

| Section              | Declares                                                                       |
| -------------------- | ------------------------------------------------------------------------------ |
| `[phases]`           | Which phases exist, in what order, with what drains and routes                 |
| `[agents]`           | Which agent specs are available and what flags they accept                     |
| `[pipeline]`         | The composition of phases, including the development iteration loop            |
| `[recovery]`         | Retry budgets, watchdog settings, and recovery transitions                     |
| `[artifacts]`        | The artifact contracts each phase must produce                                 |
| `[mcp]`              | MCP upstream configuration and transport selection                             |
| `[capabilities]`     | The capability bundle the runtime exposes                                      |

The bundled default policy lives in `ralph/policy/defaults/*.toml`. The
[policy explanation](policy-explanation.md) page walks through each section
in detail.

## How the runtime uses policy

`ralph/pipeline/orchestrator.py` is a **pure `determine_next_effect`**
function: given the current `PipelineState`, it consults the policy and
returns the next effect to execute. The effect is then handed to the
appropriate handler in `ralph/phases/`.

The runtime is intentionally thin:

- It does **not** decide which phases exist — policy decides.
- It does **not** decide which agent handles a phase — the routing layer
  consults policy and returns the agent name.
- It does **not** decide what counts as recovery — policy declares the
  transitions and the runtime enforces them.
- It does **not** decide what artifacts are required — the artifact
  contract is policy, and the runtime fails the phase if it's missing.

## Why the runtime fails closed

If policy is unsatisfiable (e.g. a chain references an unknown agent, or a
recovery transition has no budget), the runtime **fails closed** rather
than guessing. The `ralph --check-policy` command validates a policy
bundle before a real run; the validation runs the same code path the
runtime uses, so a green check means the runtime will not fail with a
policy-shaped error during the run.

## Where policy ends and code begins

The boundary is intentional:

- **Policy decides** what the pipeline *should* do for a given state.
- **Code enforces** what the pipeline *must* do (atomicity, checkpoint
  integrity, watchdog invariants, artifact validation, budget caps).

If you find yourself adding a feature to the runtime, the question to ask
is: **is this policy, or is this code?** If the behavior is project- or
team-specific, it belongs in policy. If it's a correctness invariant, it
belongs in code with a test.

## Extension points

The runtime exposes three extension points that compose with policy:

1. **Custom agent registration** — add a new agent CLI via
   `register_agent_support` (see [Adding a new agent](../agents/adding-a-new-agent.md))
2. **Custom capability bundles** — extend the capability system with new
   skills or web helpers
3. **Custom MCP upstreams** — wire a new MCP server via the `mcp.toml`
   surface

Each extension point has a policy declaration and a runtime validation
gate. You cannot register an agent that the runtime cannot find on PATH,
and you cannot declare a phase that the runtime cannot route.

## The tradeoffs

The composed shape accepts three costs:

- **Two-file minimum** — even the smallest customization requires a
  `ralph-workflow.toml` next to your `PROMPT.md`.
- **Validation is mandatory** — every policy change runs through the
  same validator the runtime uses.
- **Defaults are versioned** — the bundled policy ships with Ralph Workflow.
  Changes between versions are documented in the changelog.

The trade is **legibility**: a reader can answer "why did this run route
through this agent?" by reading one policy file, not by reading runtime
code.

## Related pages

- [Policy explanation](policy-explanation.md) — the bundled policy walkthrough
- [Configuration](configuration.md) — how to override policy in your repo
- [Advanced pipeline configuration](advanced-pipeline-configuration.md) —
  per-phase overrides
- [Phase routing](phase-routing.md) — the runtime layer that consults policy
- [The Ralph-loop](ralph-loop.md) — the simple core the pipeline composes