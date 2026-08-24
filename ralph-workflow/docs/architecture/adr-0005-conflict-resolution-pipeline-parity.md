# ADR-0005: Conflict-resolution pipeline parity

* Status: Accepted
* Date: 2026-08-23
* Supersedes: ADR-0004's retry and failover discussion

## Context

Conflict resolution runs outside the declared phase graph while Ralph Workflow
has a paused feature rebase, endpoint merge, or remote-target reconciliation.
That placement is necessary: Ralph, not an agent, owns staging, repository proof,
and Git advancement. It must not create a second recovery policy.

A local candidate cap, retry loop, or generic "candidate declined" result would
diverge from normal phase behavior. It could omit configured fallbacks, discard
chain retry/backoff semantics, or hide a launch, provider, tool-surface, or loop
failure behind a conflict-level result.

## Decision

1. Conflict resolution remains an out-of-graph drain bound to
   `rebase_conflict_resolution` in `agents.toml`. Its default chain is the
   development-style `claude` chain with `max_retries = 2` and
   `retry_delay_ms = 1000`; operators may add ordered fallback candidates.
2. A failed invocation is classified and routed through
   `RecoveryController.handle` using `rebase_conflict_resolution` pipeline
   state. The chain owns candidate ordering, same-agent retries, backoff,
   availability/cooldown behavior, and fallover. `max_fallback_agents` remains
   compatibility-only and never limits the declared chain.
3. One durable conflict context identifies work by observed feature and target
   tips, conflicted paths, index-stage object IDs, and a path scope. Feature
   integration and remote reconciliation have distinct scopes, so either may
   make progress without spending the other's limit. An unchanged failed
   identity is suppressed rather than immediately retried through an endpoint
   fallback or a later run.
4. Ralph-specific behavior stays limited to repository ownership: it rejects
   out-of-reach conflicts before an agent is charged, stages only proven
   conflicted paths, independently checks markers and unmerged entries, records
   landed rebase stops, and advances Git only after the scoped resolver ends.
5. Supervision is defined by ADR-0004. Its typed outcomes remain visible to
   recovery and operators: infrastructure/tool-surface faults and transport
   loops fail an attempt without charging conflict work; silence,
   no-progress observation, out-of-reach escalation, suppression, and an
   enabled operator cap remain distinct terminal states with an actionable next
   action.

## Consequences

A conflict attempt follows the same chain recovery contract as normal agent
work, while Git remains under Ralph Workflow control. A single-agent chain is
valid but produces a load-time warning because no fallback remains. Operators
configure candidate breadth and retry behavior on the chain, not with a second
conflict-only cap.

The durable identity prevents repeated payment for unchanged work and preserves
landed rebase work for resumption. It intentionally does not prove semantic
correctness of a resolved tree; repository-state proof decides whether Ralph may
stage and continue.

## Verification

The parity and durable-context contracts are pinned by
`tests/test_conflict_resolution_phase_parity.py`,
`tests/test_conflict_resolution_futile_loop.py`,
`tests/test_conflict_resolution_resume.py`,
`tests/test_conflict_resolution_cross_path_budget.py`,
`tests/test_conflict_resolution_rebase_loop.py`, and
`tests/recovery/`.
