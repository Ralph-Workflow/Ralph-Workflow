"""Resume target reconciliation in its owning worktree without moving the feature.

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

* the durable record's identity -- the resolving / pending-continuation
  flags, the pre-integration feature SHA, the owning worktree -- is
  intent and ownership the observation table never reads.
* the completion / publication verdicts (``COMPLETE_WITH_PROOF``,
  ``PUBLISH_AFTER_LANDING``) are derived from the record's landing proof,
  and the busy refusal (``WAIT_FOR_BOUNDED_RETRY`` /
  ``REQUIRE_INTERVENTION``) is derived from the persisted retry deadline.

The split is deliberate: the owner decides *what to do to the integration
state*, the record facts decide *whether the obligation is already
finished*.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.merge import MERGE_STATE_IN_PROGRESS, MERGE_STATE_NONE, branch_sha, merge_state
from ralph.git.merge_obstructions import ancestry_state
from ralph.git.operations import get_head_sha, is_repo_clean
from ralph.git.rebase.rebase import RebaseNoOp, RebaseSuccess, rebase_onto
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.pipeline._auto_integrate_config import configured_target as _configured_target
from ralph.pipeline._commit_completion_authority import complete_commit
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
)
from ralph.pipeline._integration_continuation import (
    _legacy_completed_rebase,
    active_operation_matches,
    continue_retained_resolution,
)
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline._pending_rebase_continue import resume_pending_rebase
from ralph.pipeline._target_reconciliation_handoff import finish_target_substep
from ralph.pipeline.auto_integrate_record import (
    MARK_RESOLVING_REBASE,
    RELEASE_DIAGNOSTIC_EVIDENCE,
    IntegrationRecord,
    RecordBusyError,
    RecordUnreadableError,
    bind_integration_record_root,
    write_record,
)
from ralph.pipeline.auto_integrate_record_store import apply_record_transition
from ralph.pipeline.auto_integrate_remote_constants import remote_target_name as _remote_target_name
from ralph.pipeline.auto_integrate_transaction import integration_transaction
from ralph.pipeline.integration_director import (
    DEFAULT_REMOTE,
    DEFAULT_TARGET_BRANCH,
)
from ralph.pipeline.integration_resolution import retained_integration_reason
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason


def busy_refusal_outcome(
    retry_deadline: float | None,
    *,
    clock: object = None,
) -> IntegrationAction:
    """Map a persisted ``RebaseState.retry_deadline`` to the typed retry decision.

    Canonical mapping lives in :mod:`ralph.pipeline._integration_diagnostic`
    (lazy import avoids an import cycle): an unexpired persisted deadline
    yields ``WAIT_FOR_BOUNDED_RETRY``; a missing, malformed, or expired
    deadline yields ``REQUIRE_INTERVENTION``.
    """
    from ralph.pipeline._integration_diagnostic import busy_refusal_outcome as _map

    return _map(retry_deadline, clock=clock)


if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver


def recover_target_resolution(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState:
    """Bind resolver receipts to their durable owner while leasing the Git owner."""
    if record.owning_worktree is None:
        return _retained(record, "target reconciliation has no owning worktree")
    owner = Path(record.owning_worktree)
    foreign = owner.resolve() != root.resolve()
    lease = integration_transaction(owner) if foreign else nullcontext(True)
    with lease as acquired:
        if not acquired or (foreign and retained_integration_reason(owner) is not None):
            return _retained(record, "target reconciliation worktree busy; recovery will retry")
        release_state = _maybe_release_target_diagnostic(owner, root, record)
        if release_state is not None:
            return release_state
        return _continue_target_reconciliation(
            owner,
            root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )


def _maybe_release_target_diagnostic(
    owner: Path,
    root: Path,
    record: IntegrationRecord,
    *,
    retry_deadline: float | None = None,
    clock: object = None,
) -> RebaseState | None:
    """Release the diagnostic evidence through the typed record store.

    Returns ``None`` when there is no diagnostic evidence OR the
    release succeeded. Returns a typed :class:`RebaseState` only for
    CHANGED verdicts (retained skip -- the only path to operator
    escalation, which means a different worktree rewrote the
    durable identity) or BUSY/UNREADABLE refusals (bounded retry
    vs intervention). The UNKNOWN verdict (expired or unparseable
    lease) is fail-open: the caller auto-releases and re-derives
    state from Git, matching the O1 contract that an older or
    pre-fix version's lease must never block a newer run.
    """
    if not record.diagnostic_evidence:
        return None
    from ralph.pipeline._diagnostic_evidence_gate import diagnostic_evidence_gate_reason
    from ralph.pipeline._integration_diagnostic import release_verified_diagnostic
    from ralph.pipeline.operation_evidence import EvidenceVerdict

    verdict = release_verified_diagnostic(owner, record)
    if verdict == EvidenceVerdict.UNCHANGED:
        return _apply_release_or_refuse(root, record, retry_deadline=retry_deadline, clock=clock)
    if verdict == EvidenceVerdict.UNKNOWN:
        # Fail-open: expired or unparseable lease -> auto-release
        # through the typed record store. Returns None on success
        # (caller proceeds) or a refusal RebaseState on BUSY /
        # UNREADABLE.
        return _apply_release_or_refuse(root, record, retry_deadline=retry_deadline, clock=clock)
    # CHANGED: a different worktree rewrote the durable identity
    # under us. This is the only path to operator escalation.
    return _retained(record, diagnostic_evidence_gate_reason(verdict))


def _continue_target_reconciliation(
    owner: Path,
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState:
    """Continue the target reconciliation; returns the final RebaseState."""
    with bind_integration_record_root(owner, root):
        continued = _continue_target_record(
            owner, root, record, config, conflict_resolver, rebase_stop_resolver
        )
        if (
            isinstance(continued, str)
            or branch_sha(owner, record.target) != continued.integrated_feature_sha
        ):
            return _retained(
                record,
                continued
                if isinstance(continued, str)
                else "target reconciliation completion does not match target",
            )
        reason = finish_target_substep(root, record)
        if reason is not None:
            return _retained(record, reason)
    return RebaseState(
        last_outcome=RebaseOutcomeKind.RECOVERED,
        last_target=record.target,
        last_reason="completed retained target reconciliation",
        fast_forwarded=False,
    )


def _apply_release_or_refuse(
    root: Path,
    record: IntegrationRecord,
    *,
    retry_deadline: float | None = None,
    clock: object = None,
) -> RebaseState | None:
    """Route the release through the typed record store; return refusal RebaseState or None.

    BUSY refusals are mapped through the PERSISTED
    ``RebaseState.retry_deadline`` (via :func:`busy_refusal_outcome`):
    an unexpired deadline yields a bounded-wait retained skip; a
    missing or expired deadline fails closed to
    DIAGNOSTIC_INTERVENTION_REQUIRED. UNREADABLE refusals always map
    to DIAGNOSTIC_INTERVENTION_REQUIRED with recovery_record_retained
    =True and bytes preserved on disk -- never bare-written over.
    Callers with access to the checkpointed RebaseState pass its
    ``retry_deadline``; without one the deadline is treated as missing
    (REQUIRE_INTERVENTION).
    """
    try:
        apply_record_transition(
            root,
            RELEASE_DIAGNOSTIC_EVIDENCE,
            diagnostic_evidence=(),
            diagnostic_ownership=None,
        )
    except RecordBusyError:
        if (
            busy_refusal_outcome(retry_deadline, clock=clock)
            is IntegrationAction.WAIT_FOR_BOUNDED_RETRY
        ):
            return _retained(
                record,
                "target reconciliation worktree busy; recovery will retry",
            )
        return RebaseState(
            last_outcome=RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
            last_reason=(
                "target reconciliation record busy with no persisted retry deadline; "
                "intervention required"
            ),
            last_target=record.target,
            recovery_record_retained=True,
            recovery_record_retained_reason=(StartupRetentionReason.REMOTE_RECONCILE_RETAINED),
        )
    except RecordUnreadableError:
        return RebaseState(
            last_outcome=RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
            last_reason=("durable integration record unreadable at release; intervention required"),
            last_target=record.target,
            recovery_record_retained=True,
            recovery_record_retained_reason=(StartupRetentionReason.OBSERVED_RETAINED_ON_DISK),
        )
    return None


def _continue_target_record(
    owner: Path,
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> IntegrationRecord | str:
    if not (
        record.resolving_rebase or record.resolving_merge or record.rebase_continue_pending
    ) and (rebase_in_progress_at(owner) or merge_state(owner) == MERGE_STATE_IN_PROGRESS):
        if not active_operation_matches(owner, record):
            return "target operation identity differs from retained ownership"
        record = record.with_transition(
            MARK_RESOLVING_REBASE,
            resolving_rebase=rebase_in_progress_at(owner),
            resolving_merge=merge_state(owner) == MERGE_STATE_IN_PROGRESS,
        )
        write_record(root, record)
    elif not record.rebase_continue_pending and _owner_reenters_against_target(
        root, record, config
    ):
        completed = _legacy_completed_rebase(
            owner,
            record.with_transition(MARK_RESOLVING_REBASE, resolving_rebase=True),
        )
        if completed is not None:
            write_record(root, completed.record)
            record = completed.record
        elif merge_state(owner) == MERGE_STATE_NONE and not rebase_in_progress_at(owner):
            started = _start_saved_target_action(owner, root, record, config)
            if isinstance(started, str):
                return started
            record = started
    kind = classify_git_operation(record)
    return (
        resume_pending_rebase(owner, record)
        if kind is GitOperationKind.AWAITING_CONTINUE
        else continue_retained_resolution(
            owner, record, config, conflict_resolver, rebase_stop_resolver
        )
    )


#: The owner's actions under which the retained target reconciliation is
#: re-entered against the target.
#:
#: ``RESOLVE_ACTIVE_OPERATION`` is the owner's repair rung: the durable
#: record claims an integration that Git does not show, so the intent is
#: stale and the boundary drops the stale flags and re-enters at step 1
#: (the automatic rebase). ``REINTEGRATE_AGAINST_TARGET`` asks for the
#: re-integration directly. Every other action leaves the retained
#: resolution to the path that owns it (``CONTINUE_GIT_OPERATION`` resumes
#: the owned operation, ``WAIT_FOR_BOUNDED_RETRY`` authorises no
#: mutation).
_REENTRY_ACTIONS: frozenset[IntegrationAction] = frozenset(
    {
        IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        IntegrationAction.REINTEGRATE_AGAINST_TARGET,
    }
)


def _owner_reenters_against_target(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
) -> bool:
    """Ask the single owner whether the integration must be re-entered.

    ``root`` is the workspace that holds the durable record (and anchors
    the durable attempt ledger). The in-flight Git operation lives in the
    owning worktree and is re-derived from Git by the caller before the
    owner is asked, so the observation never has to guess it.

    The observation is built by the director -- the one observation owner
    -- against the CONFIGURED target and remote (``main`` and ``origin``
    are defaults only), and the owner then decides on it. The boundary
    asks through the ONE seam, so the durable record facts (a retained
    obligation that demands operator intervention) are overlaid on the
    owner's answer: an intervention verdict keeps the retained
    reconciliation where it is instead of re-entering it.
    """
    target = _configured_target(config) if config is not None else record.target
    remote = _remote_target_name(config) if config is not None else DEFAULT_REMOTE
    action = decide_seam_action(
        root,
        record=record,
        agent_active=False,
        ledger_path=None,
        retained=True,
        target=target or DEFAULT_TARGET_BRANCH,
        remote=remote or DEFAULT_REMOTE,
    )
    return action in _REENTRY_ACTIONS


def _start_saved_target_action(
    owner: Path,
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
) -> IntegrationRecord | str:
    if (
        config is None
        or record.pre_target_sha is None
        or get_head_sha(owner) != record.pre_feature_sha
        or branch_sha(owner, record.target) != record.pre_feature_sha
        or not is_repo_clean(owner)
        or (
            owner.resolve() != root.resolve()
            and (
                record.initiating_feature_sha is None
                or get_head_sha(root) != record.initiating_feature_sha
            )
        )
    ):
        return "saved target reconciliation requires unchanged identities and clean owner; intervention required"
    from ralph.pipeline._pending_merge_commit import _git_value

    if _git_value(owner, "symbolic-ref", "--quiet", "--short", "HEAD") != record.target:
        return "saved target reconciliation branch owner changed; intervention required"
    retained = record.with_transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    write_record(root, retained)
    result = rebase_onto(record.pre_target_sha, repo_root=owner)
    if isinstance(result, (RebaseSuccess, RebaseNoOp)) and not rebase_in_progress_at(owner):
        head = get_head_sha(owner)
        if head is not None and ancestry_state(owner, record.pre_target_sha, head) is True:
            completion = complete_commit(
                retained,
                integrated_feature_sha=head,
                resolving_rebase=False,
            )
            retained = completion.record
            write_record(root, retained)
    return retained


def _retained(record: IntegrationRecord, reason: str) -> RebaseState:
    return RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        last_target=record.target,
        last_reason=reason,
        recovery_record_retained=True,
        recovery_record_retained_reason=StartupRetentionReason.REMOTE_RECONCILE_RETAINED,
        fast_forwarded=False,
    )
