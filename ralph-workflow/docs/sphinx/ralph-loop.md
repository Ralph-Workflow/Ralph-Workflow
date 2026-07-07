# The Ralph-loop

> **Mental model page.** This is explanation, not a how-to. For the practical
> setup path, see [Getting Started](getting-started.md).

The **Ralph loop** is the simple core pattern that Ralph Workflow composes
into a stronger workflow. It is **simple at the center, powerful in
composition**.

## The original insight

The Ralph loop is attributed to
[Geoffrey Huntley (ghuntley.com/ralph)](https://ghuntley.com/ralph). The
original insight is disarmingly simple:

> Repeat a strong prompt until the model can make real progress.

That's it. No multi-agent hierarchy, no phase router, no policy engine. Just
one prompt, fed back into the model, until the model produces something
useful.

The original Ralph loop is useful for tiny one-shot work. It does not scale
to multi-hour engineering pipelines, and it does not survive the failure
modes that show up in real software projects — bad specs, flaky tests,
ambiguous requirements, context-window exhaustion.

Ralph Workflow takes the simple core and composes around it.

## The composed version

Ralph Workflow keeps the simple core — **plan, build, verify** — and wraps
it in the machinery that real engineering needs:

- A **policy bundle** that declares phases, drains, recovery, and
  terminal conditions (see [policy-driven-pipeline](policy-driven-pipeline.md))
- A **phase router** that selects the right agent and capability bundle per
  phase (see [phase-routing](phase-routing.md))
- A **four-channel watchdog** that catches idle, stuck, and crashed agents
  before they waste hours (see [watchdogs-and-timeouts](watchdogs-and-timeouts.md))
- An **artifact lifecycle** that produces evidence per phase and
  a single terminal `development_result` (see
  [artifact-lifecycle](artifact-lifecycle.md))
- A **verification model** that gates the whole pipeline on real checks
  (see [verification-model](verification-model.md))

The simple Ralph loop is still the conceptual unit: each phase is a small
plan → build → verify loop, with the artifact from one phase becoming the
spec for the next. The composed workflow is **a Ralph loop of Ralph loops**.

## Inner loop vs outer loop

It helps to distinguish two scopes:

| Scope  | What it is                              | Lives where                                |
| ------ | --------------------------------------- | ------------------------------------------ |
| Inner  | One phase, one agent, one prompt        | `ralph/agents/invoke.py`, `ralph/phases/`  |
| Outer  | The full plan → build → verify → review pipeline | `ralph/pipeline/orchestrator.py` |

The **inner loop** is what a chat-coding user does by hand: prompt → response
→ iterate. The **outer loop** is what Ralph Workflow adds: structured
routing, retry, recovery, and review across many inner loops.

When you read the code, the inner loop is mostly in `ralph/agents/`. The
outer loop is mostly in `ralph/pipeline/` and `ralph/policy/`.

## Why the simple core matters

The simple core is what makes Ralph Workflow **legible** rather than magical.
You can always answer the question "what is happening right now?" by naming
which inner loop is running, which agent is on the hook, and which phase the
outer loop is routing.

If you find yourself confused by a Ralph Workflow run, the fastest
diagnostic is to drop one level of abstraction: look at the artifact
produced by the inner loop the outer loop is currently routing, not the
outer-loop state machine.

## Tradeoffs the simple core accepts

- **Latency:** each phase is a full agent invocation. The composed loop is
  slower than a single long session. The tradeoff buys verification,
  recovery, and reviewability.
- **Cost:** each phase is a billable agent call. Recovery, review, and
  re-runs multiply cost. The tradeoff buys not having to babysit.
- **Variance:** each phase restarts from its own artifact, so the pipeline
  carries variance between phases. The artifact handoff is what keeps
  variance bounded.

## When the simple core is enough

If your task fits one prompt and one response, the original Ralph loop is
the right tool. Use Ralph Workflow when the task doesn't fit — when there
are too many inner loops to manage by hand, when verification needs to be
rigorous, or when you want to leave the machine running while you sleep.

## Related pages

- [Policy-driven pipeline](policy-driven-pipeline.md)
- [Phase routing](phase-routing.md)
- [Concepts glossary](concepts.md)
- [Getting Started](getting-started.md)