# ADR-0004: Conflict-resolution liveness

* Status: Accepted
* Date: 2026-08-22

## Context

Conflict resolution is unusually costly to interrupt: a resolver may be actively
reading files, invoking MCP tools, editing, and running checks while a merge or
rebase remains paused. The ordinary agent watchdog includes several elapsed-time
ceilings that are appropriate for a general phase but can terminate this active
work. In addition, the standalone MCP server is a separate process, so its
process-local activity sink cannot directly refresh the parent invocation's
watchdog.

A time-based termination must remain distinguishable from a genuine lack of
activity, and the repository must not be inspected or advanced while processes
from the completed resolver can still write to it.

## Decision

1. Conflict-resolution invocations use an `activity_only` supervision profile.
   Every recognised liveness source—stdout, MCP `tools/call`, subagent progress,
   and a weighted workspace change—shares one fixed inactivity clock. The default
   silence window is 900 seconds. Session, child-wait, startup, repetition,
   post-tool, process-exit, and descendant elapsed cuts do not apply to this
   profile.
2. Round, rebase-stop, and fallback-agent limits are completed-attempt routing
   limits, not time slices. One typed resolution session spans every stop of a
   paused rebase, so its optional `total_resolution_cap_seconds` is measured
   once across the complete rebase rather than restarting at each stop. The cap
   is off by default. When an operator enables it, its result is explicitly
   `OPERATOR_CAP_REACHED`, not an idle or hang verdict.
3. The parent owns an authenticated loopback activity relay for an activity-only
   standalone MCP server. The child sender requires a bounded acknowledgement for
   each tool event. Authentication, sequence, delivery, acknowledgement, or
   receiver failures are `SUPERVISION_INFRASTRUCTURE_FAILURE`; they must not be
   degraded into `CONFLICT_INACTIVITY`. Relay controls are scrubbed from agent and
   agent-controlled child environments.
4. Before marker scanning, rebase continuation, merge commit, or deterministic
   abort, Ralph stops relay intake and reaps the scoped resolver and MCP process
   trees. Partial edits after a non-success result are deliberately discarded by
   the existing abort owner. Preserving partial edits remains an open future
   decision.

## Consequences

A productive resolver can run beyond the old elapsed limits. Operators receive
low-cadence status and termination diagnostics that name the reason, last
activity, duration, and unresolved paths. The authenticated relay adds a local
process boundary and bounded acknowledgement, but prevents MCP-only work from
being invisible to supervision. A relay fault fails safely rather than granting
an unearned liveness extension or producing a false idle diagnosis. Conflict
sessions also suppress inherited cycle-timebox and ordinary session-wrap-up
notices, which are normal-phase elapsed-time supervisors rather than evidence
of resolver inactivity.

## Verification

The deterministic regression coverage is in
`tests/test_conflict_resolution_liveness_matrix.py`,
`tests/test_conflict_resolution_supervision.py`,
`tests/mcp/test_mcp_activity_relay.py`,
`tests/test_conflict_resolution_relay_failure.py`, and
`tests/test_conflict_resolution_lifecycle.py`.
