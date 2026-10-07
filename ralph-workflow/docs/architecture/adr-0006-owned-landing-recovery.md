# ADR-0006: Owned landing recovery at standalone commit boundaries

* Status: Accepted
* Date: 2026-10-06

## Context

The ordinary dispatch invariant blocks every agent phase while a durable
integration receipt exists. Pipeline startup has a recovery supervisor;
standalone commit generation previously reached the invariant directly and
raised an uncaught exception. A separate defect deleted an unfinished landing
receipt when a target advanced or an ancestry query failed.

## Decision

Standalone commit generation delegates existing ownership to the same recovery
and supervised resolution paths as pipeline startup before inspecting its diff
or clearing artifacts. The public `prepare_commit_integration` boundary returns
an `IntegrationResolutionVerdict` and accepts typed resolver ports. It starts
no new integration without an existing receipt. A final dispatch refusal is a
typed `IntegrationDispatchBlockedError`, handled outside commit-agent retry.

Target divergence schedules owned reintegration under the existing recovery
lease. The durable `reintegrate_pending` field defaults to false for older
receipts. Action replacement uses the existing atomic record writer, with no
delete-and-recreate interval. Reintegration remains owned until Git proves the
result landed. Missing targets and failed ancestry queries retain evidence.
Disabling new automatic integration does not cancel existing ownership.

## Consequences

Commit generation can recover a prepared landing and preserve pending edits.
Corrupt evidence, a changed feature tip, a dirty tree requiring reintegration,
or a busy owner produces a visible blocker while preserving the receipt.
Recovery can resume once the blocker is removed. Completion proof remains Git
state; resolver narration cannot authorize publication.

The black-box contracts use real Git refs, history, preserved files, public
verdicts, CLI exit status, and a bridge-factory interleaving. They do not depend
on the private dispatch implementation or on mock invocation counts.

## Verification

`tests/test_retained_resolution_handoff.py` exercises standalone generation,
CLI evidence preservation, merge/rebase continuation, missing targets,
dispatch races, and target movement through the public recovery boundary.
It is already part of the required auto-integration profile in `make verify`.
