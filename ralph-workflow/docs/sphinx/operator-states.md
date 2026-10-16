# Pipeline states

This page is the **operator reference** for the closed vocabulary of
states Ralph Workflow reports while a workflow is running. When you
watch the live activity line or read a run transcript, what you see is
projected from these typed states — never from parsing prose — so what
the display says always matches what the state machine is doing.

## OperationDisplayState — the eight pipeline states

`OperationDisplayState` (defined in
`ralph.display.auto_integrate_message`, ADR-0007) is the typed
eight-category state stored on `RebaseState`. One of these is active at
any moment:

| State | Meaning |
|-------|---------|
| `INTEGRATING` | A Git rebase or merge is actively in flight. |
| `LANDING` | Integration succeeded; the local target ref is being fast-forwarded. |
| `PUBLISHING` | The local landing is complete and the result is being pushed to the configured remote (or deferred if offline). |
| `CONFLICT_RESOLUTION` | A Git conflict was detected and candidate agents are being driven to resolve the conflicted paths. |
| `EVIDENCE_DIAGNOSIS` | The diagnostic lock is held while retained integration evidence is inspected — this is deliberately distinct from conflict resolution. |
| `WAITING` | A bounded retry timer or cooldown delay is running before the next attempt. |
| `INTERVENTION_REQUIRED` | Automatic recovery is exhausted (diagnostic repair or conflict budget); a human operator must act. |
| `TERMINAL` | The operation-level final state. Maps integration outcomes such as `skipped`, `recovered`, and `remote_pushed` onto `OperationDisplayState.TERMINAL`. This is distinct from whole-pipeline terminal-success eligibility, which is governed exclusively by `ralph.pipeline._terminal_authority.is_terminal_success_eligible`. |

## IntegrationDisplayLabel — the seven display labels

`IntegrationDisplayLabel` (defined in
`ralph.display.integration_display_label`) is the closed vocabulary
shown to you in status output. Producer action/state strings are mapped
onto exactly one of these seven labels:

| Label | Meaning |
|-------|---------|
| `integration` | Git integration work (rebase/merge/continuation) is being performed. |
| `landing` | The integration result is being landed on the local target. |
| `publication` | The landed result is being published to the remote. |
| `conflict resolution` | Agents are resolving an active Git conflict. |
| `evidence diagnosis` | Retained evidence is being diagnosed to classify the failure. |
| `waiting` | A bounded retry or cooldown is in progress. |
| `intervention` | Operator intervention is required before the workflow can continue. |

## Where to read more

The full lifecycle — failure categorization, checkpoints, evidence
retention, and the recovery loop these states project — is documented in
[Recovery](recovery.md).
