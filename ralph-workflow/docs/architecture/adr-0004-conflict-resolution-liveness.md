# ADR-0004: Conflict-resolution liveness

## Supervision profile

Conflict-resolution invocations use an `activity_only` supervision profile. Every recognised liveness source — stdout, MCP `tools/call`, subagent progress, and a weighted workspace change — shares one fixed inactivity clock. The default silence window is 900 seconds (`inactivity_timeout_seconds`). Session, child-wait, startup, repetition, post-tool, process-exit, and descendant elapsed cuts do not apply to this profile.

Round, rebase-stop, and fallback-agent limits are completed-attempt routing limits, not time slices. One typed resolution session spans every stop of a paused rebase, so its optional `total_resolution_cap_seconds` is measured once across the complete rebase rather than restarting at each stop. The cap is off by default. When an operator enables it, its result is explicitly `OPERATOR_CAP_REACHED`, not an idle or hang verdict.

## Activity relay

The parent owns an authenticated loopback activity relay for an activity-only standalone MCP server. The child sender requires a bounded acknowledgement for each tool event. Authentication, sequence, delivery, acknowledgement, or receiver failures are `SUPERVISION_INFRASTRUCTURE_FAILURE`; they are not degraded into `CONFLICT_INACTIVITY`. Relay controls are scrubbed from agent and agent-controlled child environments.

## Terminal quiescence

Before marker scanning, rebase continuation, merge commit, or deterministic abort, Ralph stops relay intake and reaps the scoped resolver and MCP process trees. Landed stop SHAs and remaining conflict identity are persisted in `.ralph/conflict-resolution-progress.json` via `record_landed_stop`. Abort does not drop already-landed stops; replay starts at the first unlanded stop.

## Operator-visible outcome

A productive resolver can run beyond the elapsed limits that apply to ordinary phases. Operators receive low-cadence status and termination diagnostics that name the reason, last activity, duration, and unresolved paths. The authenticated relay adds a local process boundary and bounded acknowledgement, and a relay fault fails safely rather than granting an unearned liveness extension or producing a false idle diagnosis. Conflict sessions suppress inherited cycle-timebox and ordinary session-wrap-up notices, which are normal-phase elapsed-time supervisors rather than evidence of resolver inactivity.

Conflict resolution remains an out-of-graph drain. `RecoveryController.handle` owns the bound `rebase_conflict_resolution` chain's retries, backoff, candidate ordering, and failover, while Ralph-owned staging and Git advancement remain the conflict-specific differences.

## Verification

The deterministic regression coverage is in
`tests/test_conflict_resolution_liveness_matrix.py`,
`tests/test_conflict_resolution_supervision.py`,
`tests/mcp/test_mcp_activity_relay.py`,
`tests/test_conflict_resolution_relay_failure.py`, and
`tests/test_conflict_resolution_lifecycle.py`.
