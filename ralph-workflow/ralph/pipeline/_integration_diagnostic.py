"""Supervised inspection when retained integration lacks a completion receipt.

The diagnostic path is the single authority that decides whether the
retained-integration evidence is still trustworthy. The architecture
fixes the linked-worktree reproduction by:

* reading ONLY operation-scoped Git state through the
  :class:`OperationEvidenceCollector` (no ``git for-each-ref``, no
  full ``git ls-files --stage``, no branch reflog, no
  ``logs/HEAD``);
* bounding the diagnostic-repair attempt loop at
  :data:`DIAGNOSTIC_REPAIR_ATTEMPT_CAP` so an over-cap handoff
  emits the ``RAISE_DIAGNOSTIC_INTERVENTION`` transition and stops
  calling agents;
* routing every ``write_record(... model_copy(...))`` call site
  through the closed :class:`Transition` table on
  :class:`IntegrationRecord.with_transition`;
* routing every clear of ``diagnostic_evidence`` /
  ``diagnostic_ownership`` through the
  ``RELEASE_DIAGNOSTIC_EVIDENCE`` transition.

Decision ownership
------------------

The **mutation ladder** -- which integration rung the boundary must
climb next -- is decided by the single owner
:func:`ralph.pipeline.integration_owner.decide_integration`. This
module asks the owner and acts on its verdict; it never re-derives the
rung from the durable record.

The **record facts the pure observation table cannot express** stay
with the seam:

* ``REQUIRE_INTERVENTION`` -- the durable diagnostic-intervention flag
  (see :func:`ralph.pipeline._integration_record_facts.apply_record_facts`);
* the **completion proof** -- the four-condition Git proof below. It
  verifies the DURABLE RECORD against live Git, so the record must
  decide when it applies: the owner's live observation cannot express
  "the operation is still in flight" (:func:`_completion_proof_applies`).

The split is deliberate: the owner decides *what to do to the
integration state*, the record facts decide *whether the obligation is
already finished*.

Module layout
-------------

The diagnostic concerns live in three focused modules; this module is
the seam that composes them and re-exports their public surface:

* :mod:`ralph.pipeline._integration_diagnostic_evidence` -- the
  evidence-collector seam and the held-lease verdict;
* :mod:`ralph.pipeline._integration_diagnostic_proof` -- the
  four-condition positive operation-completion proof;
* :mod:`ralph.pipeline._integration_record_facts` -- the durable record
  facts that decide the fail-closed intervention verdict and the
  persisted bounded-retry deadline.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline._auto_integrate_record_restamp import (
    current_boot_id,
    is_owner_dead,
)
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline._integration_action import (
    IntegrationAction,
    last_outcome_reason,
)
from ralph.pipeline._integration_diagnostic_evidence import (
    EVIDENCE_LEASE_SECONDS,
    diagnostic_evidence_unchanged,
    diagnostic_evidence_verdict,
    evaluate_held_diagnostic_evidence,
    operation_evidence_collector,
    release_verified_diagnostic,
    set_operation_evidence_collector,
    snapshot_has_unreadable,
)
from ralph.pipeline._integration_diagnostic_proof import (
    _check_ancestry_proof,
    _check_atomic_transition_proof,
    _check_clean_diff_proof,
    _check_gitdir_markers_proof,
    _check_head_match,
    _check_index_tree_proof,
    _check_marker_ref_proof,
    _check_operation_completion_proof,
    _check_ownership_proof,
    _check_worktree_match,
    _has_phase_proof,
)
from ralph.pipeline._integration_record_facts import (
    apply_record_facts,
    busy_refusal_outcome,
)
from ralph.pipeline._integration_record_facts import (
    busy_refusal_state as _busy_refusal_state,
)
from ralph.pipeline.auto_integrate_record import (
    BUMP_DIAGNOSTIC_ATTEMPT,
    HOLD_DIAGNOSTIC_EVIDENCE,
    RAISE_DIAGNOSTIC_INTERVENTION,
    RELEASE_DIAGNOSTIC_EVIDENCE,
    IntegrationRecord,
    RecordBusyError,
    RecordUnreadableError,
    parse_lease,
    write_record,
)
from ralph.pipeline.auto_integrate_record_store import apply_record_transition
from ralph.pipeline.conflict_resolution.resolver_health import (
    bench_resolver,
    choose_resolver,
    clear_resolver,
)
from ralph.pipeline.integration_owner import decide_integration
from ralph.pipeline.operation_evidence import (
    OperationEvidenceCollector,
    identity_fingerprint,
)
from ralph.pipeline.operation_fact import (
    _KIND_MERGE_OBJECT_RESOLVABLE,
    _KIND_REBASE_OBJECT_RESOLVABLE,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ralph.pipeline.operation_evidence import OperationFact
    from ralph.pipeline.run_loop import _LoopContext
    from ralph.recovery.controller import RecoveryController

__all__ = [
    "DIAGNOSTIC_REPAIR_ATTEMPT_CAP",
    "EVIDENCE_LEASE_SECONDS",
    "_bump_attempt",
    "_busy_refusal_state",
    "_check_ancestry_proof",
    "_check_atomic_transition_proof",
    "_check_clean_diff_proof",
    "_check_gitdir_markers_proof",
    "_check_head_match",
    "_check_index_tree_proof",
    "_check_marker_ref_proof",
    "_check_operation_completion_proof",
    "_check_ownership_proof",
    "_check_worktree_match",
    "_complete_or_intervene",
    "_has_phase_proof",
    "_operation_root_for",
    "busy_refusal_outcome",
    "diagnose_retained_integration",
    "diagnostic_evidence_unchanged",
    "diagnostic_evidence_verdict",
    "diagnostic_needed",
    "evaluate_held_diagnostic_evidence",
    "handoff_integration_diagnostic",
    "identity_fingerprint",
    "operation_evidence_collector",
    "release_verified_diagnostic",
    "set_operation_evidence_collector",
    "snapshot_has_unreadable",
]


# Default repair-attempt cap. The diagnostic path rotates inspection
# agents on every handoff, but the rotation must terminate: an
# over-cap handoff emits the ``RAISE_DIAGNOSTIC_INTERVENTION``
# transition and stops calling agents. Operators can raise the cap
# via ``conflict_resolution.diagnostic_repair_attempt_cap`` in
# policy configuration; the default is intentionally low so the
# production loop is broken on the very first incident.
DIAGNOSTIC_REPAIR_ATTEMPT_CAP: int = 5


def _operation_root_for(record: IntegrationRecord, default_root: Path) -> Path:
    """Single source of truth for the diagnostic's operation root.

    The diagnostic only switches to ``record.owning_worktree`` when
    the operation is a ``target_reconcile`` AND the path still
    exists on disk; otherwise it falls back to the caller-supplied
    ``default_root``. Four call sites used to implement this gate
    with subtly different predicates; this helper is the canonical
    rule every site must consult.
    """
    if (
        record.operation_kind == "target_reconcile"
        and record.owning_worktree
        and DEFAULT_FILE_BACKEND.exists(Path(record.owning_worktree))
    ):
        return Path(record.owning_worktree)
    return default_root


def _raise_intervention(record: IntegrationRecord, reason: str) -> IntegrationRecord:
    """Emit the ``RAISE_DIAGNOSTIC_INTERVENTION`` transition for ``record``.

    Single authority for the operator-intervention emission.
    """
    return record.with_transition(
        RAISE_DIAGNOSTIC_INTERVENTION,
        diagnostic_intervention_required=True,
        repair_cap_reason=reason,
    )


def _bump_attempt(
    record: IntegrationRecord,
    exc: str,
    *,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    cap_reason: str = "diagnostic repair exhausted; operator intervention required",
) -> IntegrationRecord:
    """Emit ``BUMP_DIAGNOSTIC_ATTEMPT`` and enforce ``DIAGNOSTIC_REPAIR_ATTEMPT_CAP``.

    Single authority for the attempt-counter emission: when the bumped
    count reaches the cap, the returned record additionally carries the
    ``RAISE_DIAGNOSTIC_INTERVENTION`` transition with ``cap_reason``.
    The counter is clamped so it never increments past the cap.
    """
    new_attempts = min(record.merge_commit_repair_attempts + 1, repair_attempt_cap)
    bumped = record.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        merge_commit_repair_attempts=new_attempts,
        repair_last_error=exc,
    )
    if new_attempts >= repair_attempt_cap:
        return _raise_intervention(bumped, cap_reason)
    return bumped


def diagnostic_needed(root: Path, record: IntegrationRecord) -> bool:
    from ralph.git.merge import MERGE_STATE_NONE, MERGE_STATE_UNKNOWN, merge_state, unmerged_paths
    from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
    from ralph.pipeline._retained_resolution_scope import original_conflict_paths

    if record.diagnostic_evidence:
        return True
    operation_root = Path(record.owning_worktree) if record.owning_worktree else root
    try:
        if classify_git_operation(record) is GitOperationKind.AWAITING_CONTINUE:
            from ralph.pipeline._pending_rebase_continue import pending_rebase_identity_matches

            return not pending_rebase_identity_matches(operation_root, record)
        if classify_git_operation(record) is GitOperationKind.AWAITING_MERGE_COMMIT:
            from ralph.pipeline._pending_merge_commit import pending_merge_identity_matches

            return not pending_merge_identity_matches(operation_root, record)
        unmerged = unmerged_paths(operation_root)
        unreadable = "<unmerged-path-query-failed>" in unmerged
        missing_scope = (record.resolving_merge or record.resolving_rebase) and not (
            record.resolving_paths or unmerged or original_conflict_paths(operation_root)
        )
        if unreadable or missing_scope:
            return True
        merge = merge_state(operation_root)
        return merge == MERGE_STATE_UNKNOWN or (
            merge == MERGE_STATE_NONE and not rebase_in_progress_at(operation_root)
        )
    except Exception:
        return True


def _next_resolver(controller: RecoveryController, agents: Sequence[str]) -> str | None:
    """Return the next resolver to call, or ``None`` (after saying why) while all are benched."""
    choice = choose_resolver(controller, agents)
    if choice.agent is None:
        logger.warning(
            "Integration diagnostic: every resolver is cooling down; next one is "
            "selectable in {:.0f}s",
            choice.wait_ms / 1000.0,
        )
    return choice.agent


def _invoke_and_judge(
    agent: str,
    prompt: Path,
    invoke: Callable[[str, Path], bool],
    controller: RecoveryController,
    inspection_root: Path,
    record: IntegrationRecord,
    col: OperationEvidenceCollector,
    failure_detail: Callable[[], BaseException | str | None] | None,
) -> bool:
    """Invoke ``agent``; clear it on verified proof, bench it on every other way out.

    An invocation that raises is benched before the error propagates, so a
    crashing resolver cannot be re-offered on the next pass.
    """
    try:
        invoked_ok = invoke(agent, prompt)
        verified = (
            invoked_ok
            and _check_operation_completion_proof(inspection_root, record, collector=col)[0]
        )
    except Exception as exc:
        bench_resolver(controller, agent, exc)
        raise
    if verified:
        clear_resolver(controller, agent)
    else:
        bench_resolver(controller, agent, _failure_text(agent, failure_detail))
    return invoked_ok


def _failure_text(
    agent: str, failure_detail: Callable[[], BaseException | str | None] | None
) -> BaseException | str:
    """The real failure of the attempt, or a statement that it produced no proof."""
    detail = failure_detail() if failure_detail is not None else None
    return detail or f"resolver '{agent}' ended without a verified completion proof"


def diagnose_retained_integration(
    ctx: _LoopContext,
    failure: str,
    *,
    invoke: Callable[[str, Path], bool] | None = None,
    collector: OperationEvidenceCollector | None = None,
) -> bool:
    """Inspect missing evidence under both worktree leases; never accept agent success as proof."""
    from contextlib import ExitStack

    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_transaction import integration_transaction
    from ralph.pipeline.conflict_resolution.session import (
        ResolutionSession,
        invoke_resolution_agent,
        resolution_chain_agents,
    )
    from ralph.workspace.context import workspace_context
    from ralph.workspace.scope import WorkspaceScope

    pipeline_deps = ctx.pipeline_deps
    if pipeline_deps is None:
        logger.critical("Integration evidence inspection requires agent dependencies: {}", failure)
        return False
    with ExitStack() as leases:
        if (
            not leases.enter_context(integration_transaction(ctx.workspace_scope.root))
            or (record := read_record(ctx.workspace_scope.root)) is None
        ):
            return False
        if (
            record.diagnostic_intervention_required
            or record.merge_commit_repair_attempts >= DIAGNOSTIC_REPAIR_ATTEMPT_CAP
        ):
            _fingerprint, held_at = parse_lease(record.diagnostic_ownership)
            is_expired_lease = held_at is None or (time.time() - held_at) > EVIDENCE_LEASE_SECONDS
            is_old_flagged = not record.diagnostic_evidence and not record.diagnostic_ownership
            if is_expired_lease or is_old_flagged or is_owner_dead(record):
                try:
                    apply_record_transition(
                        ctx.workspace_scope.root,
                        RELEASE_DIAGNOSTIC_EVIDENCE,
                        diagnostic_evidence=(),
                        diagnostic_ownership=None,
                        diagnostic_owner_pid=None,
                        diagnostic_owner_boot_id=None,
                        diagnostic_intervention_required=False,
                        repair_cap_reason=None,
                    )
                    refreshed = read_record(ctx.workspace_scope.root)
                    if refreshed is not None:
                        record = refreshed
                except Exception:
                    pass
            if (
                record.diagnostic_intervention_required
                or record.merge_commit_repair_attempts >= DIAGNOSTIC_REPAIR_ATTEMPT_CAP
            ):
                if not record.diagnostic_intervention_required:
                    write_record(
                        ctx.workspace_scope.root,
                        _raise_intervention(
                            record,
                            "diagnostic repair exhausted; operator intervention required",
                        ),
                    )
                return False

        operation_root = _operation_root_for(record, ctx.workspace_scope.root)
        if operation_root != ctx.workspace_scope.root and not leases.enter_context(
            integration_transaction(operation_root)
        ):
            return False

        effective_invoke: Callable[[str, Path], bool]
        attempt_session = ResolutionSession()
        if invoke is None:

            def _default_invoke(agent: str, prompt: Path) -> bool:
                attempt_session.last_attempt_failure = None
                with workspace_context(operation_root):
                    return invoke_resolution_agent(
                        agent_name=agent,
                        prompt_path=prompt,
                        config=ctx.config,
                        pipeline_deps=pipeline_deps,
                        workspace_scope=WorkspaceScope(operation_root),
                        policy_bundle=ctx.policy_bundle,
                        display=ctx.active_display,
                        display_context=ctx.display_context,
                        operator_cap_seconds=ctx.config.conflict_resolution.total_resolution_cap_seconds,
                        require_completion_evidence=True,
                        session=attempt_session,
                    )

            effective_invoke = _default_invoke
        else:
            effective_invoke = invoke

        try:
            col = collector if collector is not None else operation_evidence_collector()
            # The diagnostic path no longer consumes the evidence
            # snapshot for the release verdict; the lease in
            # ``diagnostic_ownership`` is the sole release authority.
            # The collector's snapshot is preserved public API
            # (tests in :mod:`tests.test_operation_evidence` exercise
            # the snapshot). The diagnostic path no longer compares
            # the snapshot to held evidence, but it still consults
            # the snapshot for UNREADABLE detection (via
            # :func:`snapshot_has_unreadable` inside the proof). Run
            # the snapshot here so a collector crash surfaces in the
            # ``except`` branch and bumps the attempt counter
            # (the OLD evidence-collecting path relied on this).
            before: tuple[tuple[str, str], ...] = ()
            if not record.diagnostic_evidence or not record.diagnostic_ownership:
                lease = f"{identity_fingerprint(record)}:{time.time()}"
                record = record.with_transition(
                    HOLD_DIAGNOSTIC_EVIDENCE,
                    diagnostic_evidence=before,
                    diagnostic_ownership=lease,
                    diagnostic_owner_pid=os.getpid(),
                    diagnostic_owner_boot_id=current_boot_id(),
                )
                write_record(ctx.workspace_scope.root, record)
            col.snapshot(operation_root, record)
            released = handoff_integration_diagnostic(
                root=ctx.workspace_scope.root,
                record=record,
                failure=failure,
                agents=tuple(
                    agent
                    for agent in resolution_chain_agents(ctx.policy_bundle)
                    if ctx.registry.get(agent) is not None
                ),
                invoke=effective_invoke,
                collector=col,
                controller=ctx.controller,
                failure_detail=lambda: attempt_session.last_attempt_failure,
            )
            retained = read_record(ctx.workspace_scope.root)
            return bool(
                retained is not None
                and identity_fingerprint(retained) == identity_fingerprint(record)
                and released
                and not retained.diagnostic_evidence
                and retained.diagnostic_ownership is None
            )
        except Exception as exc:
            logger.critical("Integration diagnostic retained ownership after failure: {}", exc)
            current = read_record(ctx.workspace_scope.root) or record
            target = _bump_attempt(current, str(exc), cap_reason=f"diagnostic failure: {exc}")
            write_record(ctx.workspace_scope.root, target)
            return False


def _pre_pass_intervention(
    root: Path,
    record: IntegrationRecord,
    backend: FileBackend,
    reason: str,
) -> bool:
    intervention = _raise_intervention(record, reason)
    write_record(root, intervention, backend=backend)
    logger.critical(
        "Operation completion proof failed ({}); operator intervention required",
        reason,
    )
    return False


def _completion_proof_applies(record: IntegrationRecord) -> bool:
    """Whether the four-condition completion proof applies to ``record``.

    ADR-0008 section 3: the proof is evaluated when the DURABLE RECORD
    says the Git operation is still in flight (a continuation or a merge
    commit is pending) or that the integration has landed
    (``phase == "integrated"``). These are record facts, not
    observations: the proof verifies the durable record against live
    Git, so the record must be the trigger.
    """
    kind = classify_git_operation(record)
    if kind in (GitOperationKind.AWAITING_CONTINUE, GitOperationKind.AWAITING_MERGE_COMMIT):
        return True
    return record.is_integrated


def _run_completion_pre_pass(
    *,
    root: Path,
    inspection_root: Path,
    record: IntegrationRecord,
    snapshot: tuple[OperationFact, ...],
    next_action: IntegrationAction,
    backend: FileBackend,
    collector: OperationEvidenceCollector | None = None,
) -> bool | None:
    """Run the fail-closed pre-pass that precedes every diagnostic agent call.

    ``next_action`` is the owner's mutation-ladder answer, overlaid with
    the durable record facts. The completion-proof trigger is a RECORD
    FACT (:func:`_completion_proof_applies`): the four-condition proof
    verifies the durable record against live Git, so the record decides
    when it applies.

    Returns:
        ``None`` when the pre-pass does not decide (the caller may
        offer a resolver), otherwise the pre-pass outcome.
    """
    for fact in snapshot:
        if fact.kind in (
            _KIND_MERGE_OBJECT_RESOLVABLE,
            _KIND_REBASE_OBJECT_RESOLVABLE,
        ) and fact.value in ("False", "false"):
            return _pre_pass_intervention(
                root,
                record,
                backend,
                "unreachable marker object; operator intervention required",
            )

    if next_action is IntegrationAction.REQUIRE_INTERVENTION:
        reason = last_outcome_reason(record) or "operator intervention required"
        return _pre_pass_intervention(root, record, backend, reason)

    if _completion_proof_applies(record):
        return _complete_or_intervene(
            root=root,
            inspection_root=inspection_root,
            record=record,
            backend=backend,
            collector=collector,
        )

    return None


def _complete_or_intervene(
    *,
    root: Path,
    inspection_root: Path,
    record: IntegrationRecord,
    backend: FileBackend,
    collector: OperationEvidenceCollector | None,
) -> bool:
    """Run the positive-completion pre-pass through the typed record store."""
    proof_ok, proof_reason = _check_operation_completion_proof(
        inspection_root, record, collector=collector
    )
    if not proof_ok:
        return _pre_pass_intervention(
            root, record, backend, f"operation completion proof failed: {proof_reason}"
        )
    try:
        apply_record_transition(
            root,
            RELEASE_DIAGNOSTIC_EVIDENCE,
            backend=backend,
            diagnostic_evidence=(),
            diagnostic_ownership=None,
        )
    except RecordBusyError:
        # Fail closed: no write, no fresh retry allowance. The caller
        # decides bounded wait vs intervention from the PERSISTED
        # ``RebaseState.retry_deadline`` (see
        # :func:`ralph.pipeline._integration_record_facts.busy_refusal_outcome`);
        # with no deadline in scope this is REQUIRE_INTERVENTION. The
        # record bytes stay untouched on disk.
        logger.warning("Release busy; record bytes preserved for the bounded-retry authority")
        return False
    except RecordUnreadableError:
        # Fail closed: NEVER bare-write over UNREADABLE bytes. The bytes
        # on disk are the only proof of the interrupted integration; the
        # typed DIAGNOSTIC_INTERVENTION_REQUIRED outcome is derived from
        # the unchanged UNREADABLE read on the next seam.
        logger.critical(
            "Release refused: durable record unreadable; intervention required; "
            "unreadable bytes preserved"
        )
        return False
    logger.info("Positive operation-completion proof verified; released diagnostic evidence")
    return True


def _settle_exhausted_cap(
    root: Path,
    inspection_root: Path,
    record: IntegrationRecord,
    col: OperationEvidenceCollector,
    backend: FileBackend,
) -> bool:
    """Release the hold when Git shows no diagnostic is needed; otherwise raise intervention."""
    proof_ok, _ = _check_operation_completion_proof(inspection_root, record, collector=col)
    if proof_ok or not diagnostic_needed(inspection_root, record):
        try:
            apply_record_transition(
                root,
                RELEASE_DIAGNOSTIC_EVIDENCE,
                backend=backend,
                diagnostic_evidence=(),
                diagnostic_ownership=None,
                diagnostic_owner_pid=None,
                diagnostic_owner_boot_id=None,
                diagnostic_intervention_required=False,
                repair_cap_reason=None,
            )
        except (RecordBusyError, RecordUnreadableError):
            return False
        logger.info("Automated recovery re-derived state; released diagnostic evidence")
        return True
    write_record(
        root,
        _raise_intervention(
            record,
            "diagnostic repair exhausted; operator intervention required",
        ),
        backend=backend,
    )
    logger.critical(
        "Integration diagnostic repair exhausted after {} attempts; operator intervention required",
        record.merge_commit_repair_attempts,
    )
    return False


def handoff_integration_diagnostic(
    *,
    root: Path,
    record: IntegrationRecord,
    failure: str,
    agents: Sequence[str],
    invoke: Callable[[str, Path], bool],
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    collector: OperationEvidenceCollector | None = None,
    failure_detail: Callable[[], BaseException | str | None] | None = None,
    controller: RecoveryController,
) -> bool:
    """Offer inspection candidates by health without authorizing publication or cleanup.

    Candidates come from the shared resolver health ledger (``controller``):
    one that ended an attempt without a verified result is benched with
    exponential backoff and is not offered again until its cooldown elapses.
    While every candidate is benched nothing is invoked and no attempt is
    spent, so the caller's retry cadence costs nothing.

    The diagnostic-repair attempt counter is bounded at
    ``repair_attempt_cap``. When the cap is reached, the function
    emits the ``RAISE_DIAGNOSTIC_INTERVENTION`` transition and
    returns ``False`` without calling any agent -- the next run
    loop iteration will see ``diagnostic_intervention_required``
    and surface ``REPORT_INTERVENTION_REQUIRED`` to the operator.
    """
    inspection_root = _operation_root_for(record, root)
    col = collector if collector is not None else operation_evidence_collector()
    snapshot = col.snapshot(inspection_root, record)
    # The owner decides the MUTATION LADDER for this seam; the durable
    # record facts decide whether the obligation is already finished
    # (the completion proof in the pre-pass below).
    owner_action, _owner_reason = decide_integration(root, record=record)
    next_action = apply_record_facts(owner_action, record)

    pre_pass_result = _run_completion_pre_pass(
        root=root,
        inspection_root=inspection_root,
        record=record,
        snapshot=snapshot,
        next_action=next_action,
        backend=backend,
        collector=col,
    )
    if pre_pass_result is not None:
        return pre_pass_result

    if record.merge_commit_repair_attempts >= repair_attempt_cap:
        return _settle_exhausted_cap(root, inspection_root, record, col, backend)
    if not agents:
        target_record = _bump_attempt(
            record,
            "no eligible diagnostic agent available",
            repair_attempt_cap=repair_attempt_cap,
        )
        if (
            target_record.diagnostic_intervention_required
            and not record.diagnostic_intervention_required
        ):
            logger.critical(
                "Integration diagnostic repair exhausted after {} attempts; "
                "operator intervention required",
                target_record.merge_commit_repair_attempts,
            )
        write_record(root, target_record, backend=backend)
        return False
    agent = _next_resolver(controller, agents)
    if agent is None:
        return False
    prompt = root / ".agent" / "tmp" / "integration_diagnostic_prompt.md"
    content = (
        "Inspect the retained integration blocker and repair only its environment.\n"
        f"Diagnostic data: {failure[:8192]}\n"
        f"Recorded target: {record.target}\n"
        f"Original feature: {record.pre_feature_sha}\n"
        f"Original target: {record.pre_target_sha}\n"
        f"Operation worktree: {record.owning_worktree or root}\n"
        "Completion evidence is missing or unreadable. Examine Git history, reflogs, "
        "operation metadata, executable availability, and the command diagnostic. "
        "Restore access to existing evidence only when independently justified. "
        "Do not create, alter, or delete recovery records, receipts, refs, indexes, "
        "operation metadata, hooks, signing configuration, or source files. "
        "Do not reset, abort, stash, commit, rebase, merge, stage, or bypass checks. "
        "Preserve all unfinished work. Declare_complete with observed cause, "
        "evidence locations, and any environment repair. Agent success does not "
        "authorize landing; Ralph independently retries and verifies Git evidence.\n"
    )
    backend.mkdir(prompt.parent, parents=True, exist_ok=True)
    write_text_if_changed(backend, prompt, content)
    write_record(
        root,
        _bump_attempt(record, failure[:8192], repair_attempt_cap=repair_attempt_cap),
        backend=backend,
    )
    return _invoke_and_judge(
        agent, prompt, invoke, controller, inspection_root, record, col, failure_detail
    )
