"""Target-reconciliation handoff: discharge the target substep, adopt a scheduled landing.

Decision ownership
-------------------

The **mutation ladder** -- which integration rung the boundary must climb
next -- is decided by the single owner
:func:`ralph.pipeline.integration_owner.decide_integration`, reached through
the one seam :func:`ralph.pipeline._integration_owner_seam.decide_seam_action`.
This module asks the seam and acts on its verdict; it never re-derives the
rung from the durable record.

The **record facts the pure observation table cannot express** stay with
the seam:

* the scheduled landing's *identity* -- its operation kind, its
  re-integration intent, its target and its pre-integration feature SHA --
  is durable intent. The observation table reads Git, never intent, so
  the seam keeps the identity checks and asks the owner only about the
  rung.
* the completion / publication verdicts (``COMPLETE_WITH_PROOF``,
  ``PUBLISH_AFTER_LANDING``) are derived from the record's landing proof.

The split is deliberate: the owner decides *what to do to the integration
state*, the record facts decide *whether the obligation is already
finished*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge import branch_sha
from ralph.git.operations import get_head_sha
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline.auto_integrate_record import (
    MARK_REINTEGRATE_PENDING,
    START_INTEGRATING,
    IntegrationRecord,
    clear_record,
    read_record,
    write_record,
)
from ralph.pipeline.integration_director import DEFAULT_REMOTE

if TYPE_CHECKING:
    from pathlib import Path


#: The owner's actions under which the scheduled landing may be adopted.
#:
#: ``RESOLVE_ACTIVE_OPERATION`` is the owner's repair rung: the durable
#: record claims an integration that Git does not show, so the intent is
#: stale and the boundary re-enters at step 1 (the automatic rebase).
#: ``REINTEGRATE_AGAINST_TARGET`` asks for the re-integration directly.
#: ``LAND_TARGET`` means the work is already contained in the target: the
#: integration runs its idempotent land and releases the record.
#:
#: ``CONTINUE_GIT_OPERATION`` (an owned operation is in flight) and
#: ``WAIT_FOR_BOUNDED_RETRY`` (no mutation is authorised right now) leave
#: the durable record with the recovery path that owns it.
_ADOPTED_ACTIONS: frozenset[IntegrationAction] = frozenset(
    {
        IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        IntegrationAction.REINTEGRATE_AGAINST_TARGET,
        IntegrationAction.LAND_TARGET,
    }
)


def finish_target_substep(root: Path, record: IntegrationRecord) -> str | None:
    """Discharge the target substep and schedule the landing of the feature.

    A reconciliation that ran in its own worktree leaves the feature
    branch behind the reconciled target. This function either clears the
    record (the reconciliation ran in this worktree) or schedules the
    landing as a fresh integration owned by this worktree.
    """
    if record.owning_worktree is None:
        return "target reconciliation owner missing; intervention required"
    from pathlib import Path

    if Path(record.owning_worktree).resolve() == root.resolve():
        clear_record(root)
        return None
    if record.initiating_feature_sha is None:
        return (
            "legacy target reconciliation lacks initiating feature identity; intervention required"
        )
    if get_head_sha(root) != record.initiating_feature_sha:
        return "initiating feature changed during target reconciliation; landing withheld"
    target_sha = branch_sha(root, record.target)
    if target_sha is None:
        return "target reconciliation completion unreadable; landing withheld"
    scheduled = record.with_transition(
        START_INTEGRATING,
        phase="integrating",
        target=record.target,
        pre_feature_sha=record.initiating_feature_sha,
        pre_target_sha=target_sha,
        operation_kind="feature_integrate",
        owning_worktree=None,
        initiating_feature_sha=None,
    ).with_transition(
        MARK_REINTEGRATE_PENDING,
        reintegrate_pending=True,
    )
    write_record(root, scheduled)
    return "target reconciled; initiating feature landing scheduled for recovery"


def scheduled_landing(root: Path, target: str) -> IntegrationRecord | None:
    """Return the scheduled landing record when the owner authorises the landing.

    The durable record's identity -- the operation kind, the
    re-integration intent, the target and the pre-integration feature SHA
    -- is a record fact and stays with the seam. The *decision* whether
    the boundary may act on that record belongs to the single owner: this
    function asks through the one seam
    (:func:`ralph.pipeline._integration_owner_seam.decide_seam_action`) and
    adopts the record only for the actions that keep the integration
    owed (see :data:`_ADOPTED_ACTIONS`).

    Returning ``None`` leaves the durable record on disk; the caller then
    refuses to start a fresh integration over it and the recovery path
    keeps ownership of the obligation.

    The observation is built by the director -- the one observation owner
    -- against the branch this handoff targets, and the owner then
    decides on it.
    """
    record = read_record(root)
    if record is None:
        return None
    if record.operation_kind != "feature_integrate" or not record.reintegrate_pending:
        return None
    if record.target != target:
        return None
    if get_head_sha(root) != record.pre_feature_sha:
        return None
    action = decide_seam_action(
        root,
        record=record,
        agent_active=False,
        ledger_path=None,
        target=target,
        remote=DEFAULT_REMOTE,
    )
    return record if action in _ADOPTED_ACTIONS else None


__all__ = [
    "finish_target_substep",
    "scheduled_landing",
]
