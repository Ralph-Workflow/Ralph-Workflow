# ADR-0006: Owned landing recovery at standalone commit boundaries

* Related: [ADR-0007](adr-0007-integration-source-of-truth.md), [ADR-0009](adr-0009-pipeline-source-of-truth-ownership.md)

## Architecture

The ordinary dispatch invariant blocks every agent phase while a durable integration receipt exists. Pipeline startup has a recovery supervisor, and standalone commit generation runs the same supervisor.

Standalone commit generation delegates existing ownership to the same recovery
and supervised resolution paths as pipeline startup before inspecting its diff
or clearing artifacts. The public `prepare_commit_integration` boundary returns
an `IntegrationResolutionVerdict` and accepts typed resolver ports. It starts
no new integration without an existing receipt. A final dispatch refusal is a
typed `IntegrationDispatchBlockedError`, handled outside commit-agent retry.

Target divergence schedules owned reintegration under the existing recovery
lease. A record without the durable `reintegrate_pending` field is read with
`reintegrate_pending = false`. Action replacement uses the atomic record
writer, with no delete-and-recreate interval. Reintegration remains owned until
Git proves the result landed. Missing targets and failed ancestry queries
retain evidence. Disabling new automatic integration does not cancel existing
ownership.

Foreign target reconciliation records the initiating feature identity. Finishing
that target operation atomically schedules the feature landing; it cannot release
the initiating obligation. A foreign receipt without that identity retains
evidence and requires intervention rather than guessing the protected feature.

## Boundaries

Commit generation can recover a prepared landing and preserve pending edits.
Corrupt evidence, a changed feature tip, a dirty tree requiring reintegration,
or a busy owner produces a visible blocker while preserving the receipt.
Recovery can resume once the blocker is removed. Completion proof remains Git
state; resolver narration cannot authorize publication.

## Verification

`tests/test_retained_resolution_handoff.py` exercises standalone generation,
CLI evidence preservation, merge/rebase continuation, missing targets,
dispatch races, and target movement through the public recovery boundary.
It is already part of the required auto-integration profile in `make verify`.

The black-box contracts use real Git refs, history, preserved files, public
verdicts, CLI exit status, and a bridge-factory interleaving. They do not depend
on the private dispatch implementation or on mock invocation counts.
