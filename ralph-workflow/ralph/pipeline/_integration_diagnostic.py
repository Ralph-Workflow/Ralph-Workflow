from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline import _terminal_emitter as terminal_emitter
from ralph.pipeline._auto_integrate_record_restamp import (
    current_boot_id,
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
from ralph.pipeline._integration_diagnostic_handoff import (
    _invoke_and_judge,
    budget_spent_record,
    escalate_diagnostic_budget_to_ladder,
    escalate_record_fact_outcome,
    ladder_terminal_state,
    next_resolver,
)
from ralph.pipeline._integration_diagnostic_prepass import (
    _completion_proof_applies,
    _operation_root_for,
    _settle_spent_budget,
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
from ralph.pipeline._integration_diagnostic_state import (
    DIAGNOSTIC_BUDGET_SPENT_REASON,
)
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline._integration_record_facts import (
    busy_refusal_outcome,
)
from ralph.pipeline._integration_record_facts import (
    busy_refusal_state as _busy_refusal_state,
)
from ralph.pipeline.auto_integrate_record import (
    BUMP_DIAGNOSTIC_ATTEMPT,
    HOLD_DIAGNOSTIC_EVIDENCE,
    RELEASE_DIAGNOSTIC_EVIDENCE,
    IntegrationRecord,
    RecordBusyError,
    RecordUnreadableError,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_record_store import apply_record_transition
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.operation_evidence import (
    OperationEvidenceCollector,
    identity_fingerprint,
)
from ralph.pipeline.operation_fact import (
    _KIND_MERGE_OBJECT_RESOLVABLE,
    _KIND_REBASE_OBJECT_RESOLVABLE,
)
from ralph.workspace.context import WorkspaceContextError, workspace_context

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ralph.pipeline._integration_ladder_seams import (
        EnvironmentProbe,
        FileQuarantine,
        GitPlumbing,
    )
    from ralph.pipeline.integration_actions import Clock
    from ralph.pipeline.integration_attempt_ledger import AttemptLedger
    from ralph.pipeline.operation_evidence import OperationFact
    from ralph.pipeline.run_loop import _LoopContext
    from ralph.recovery.controller import RecoveryController

__all__ = [
    "DIAGNOSTIC_BUDGET_SPENT_REASON",
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
    "budget_spent_record",
    "busy_refusal_outcome",
    "diagnose_retained_integration",
    "diagnostic_evidence_unchanged",
    "diagnostic_evidence_verdict",
    "diagnostic_needed",
    "escalate_diagnostic_budget_to_ladder",
    "escalate_record_fact_outcome",
    "evaluate_held_diagnostic_evidence",
    "handoff_integration_diagnostic",
    "identity_fingerprint",
    "operation_evidence_collector",
    "release_verified_diagnostic",
    "set_operation_evidence_collector",
    "snapshot_has_unreadable",
]


DIAGNOSTIC_REPAIR_ATTEMPT_CAP: int = 5


def _bump_attempt(
    record: IntegrationRecord,
    exc: str,
    *,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    cap_reason: str = DIAGNOSTIC_BUDGET_SPENT_REASON,
) -> IntegrationRecord:
    new_attempts = min(record.merge_commit_repair_attempts + 1, repair_attempt_cap)
    bumped = record.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        merge_commit_repair_attempts=new_attempts,
        repair_last_error=exc,
    )
    if new_attempts >= repair_attempt_cap:
        return budget_spent_record(bumped, reason=cap_reason, repair_attempt_cap=repair_attempt_cap)
    return bumped


def diagnostic_needed(root: Path, record: IntegrationRecord) -> bool:
    from ralph.git.operation_snapshot import OperationState, observe_operation_snapshot
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
        snapshot = observe_operation_snapshot(operation_root, target=record.target)
        unmerged = snapshot.unmerged_paths
        unreadable = not snapshot.unmerged_query_ok
        missing_scope = (record.resolving_merge or record.resolving_rebase) and not (
            record.resolving_paths or unmerged or original_conflict_paths(operation_root)
        )
        if unreadable or missing_scope:
            return True
        return snapshot.operation_state in (
            OperationState.UNKNOWN,
            OperationState.NONE,
            OperationState.DETACHED,
        )
    except Exception:
        return True


def _handle_diagnostic_config_failure(
    root: Path,
    record: IntegrationRecord,
    exc: WorkspaceContextError,
) -> None:
    reason_text = exc.fix
    logger.warning(
        "Integration diagnostic: target worktree configuration error: {}; {}",
        exc,
        reason_text,
    )
    current = read_record(root) or record
    target = current.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        repair_last_error=reason_text,
    ).model_copy(update={"last_failure_cause": ResolutionFailureCause.CONFIG_FAILURE})
    write_record(root, target)


def _handle_diagnostic_generic_failure(
    root: Path,
    record: IntegrationRecord,
    exc: Exception,
    *,
    ledger: AttemptLedger | None,
    git: GitPlumbing | None,
    fs: FileQuarantine | None,
    env: EnvironmentProbe | None,
    clock: Clock | None,
) -> None:
    terminal_emitter.critical("Integration diagnostic retained ownership after failure: {}", exc)
    current = read_record(root) or record
    target = _bump_attempt(current, f"diagnostic failure: {exc}")
    write_record(root, target)
    if target.merge_commit_repair_attempts >= DIAGNOSTIC_REPAIR_ATTEMPT_CAP:
        try:
            escalate_diagnostic_budget_to_ladder(
                root,
                target,
                reason=f"diagnostic failure: {exc}",
                ledger=ledger,
                git=git,
                fs=fs,
                env=env,
                clock=clock,
                target=target.target,
            )
        except Exception:
            terminal_emitter.critical(
                "Integration diagnostic budget spent but ladder escalation raised; "
                "the durable ledger keeps the climb and the next seam resumes it",
            )


def diagnose_retained_integration(
    ctx: _LoopContext,
    failure: str,
    *,
    invoke: Callable[[str, Path], bool] | None = None,
    collector: OperationEvidenceCollector | None = None,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
) -> bool:
    from contextlib import ExitStack

    from ralph.pipeline.auto_integrate_transaction import integration_transaction
    from ralph.pipeline.conflict_resolution.session import (
        ResolutionSession,
        invoke_resolution_agent,
        resolution_chain_agents,
    )
    from ralph.workspace.scope import WorkspaceScope

    pipeline_deps = ctx.pipeline_deps
    if pipeline_deps is None:
        terminal_emitter.critical(
            "Integration evidence inspection requires agent dependencies: {}", failure
        )
        return False
    with ExitStack() as leases:
        root_lease = leases.enter_context(integration_transaction(ctx.workspace_scope.root))
        if not root_lease.acquired or (record := read_record(ctx.workspace_scope.root)) is None:
            return False
        record, budget_spent = _settle_spent_budget(
            ctx.workspace_scope.root,
            record,
            ledger=ledger,
            git=git,
            fs=fs,
            env=env,
            clock=clock,
        )
        if budget_spent:
            return False

        operation_root = _operation_root_for(record, ctx.workspace_scope.root)
        if operation_root != ctx.workspace_scope.root:
            operation_lease = leases.enter_context(integration_transaction(operation_root))
            if not operation_lease.acquired:
                return False

        effective_invoke: Callable[[str, Path], bool]
        attempt_session = ResolutionSession()
        if invoke is None:

            def _default_invoke(agent: str, prompt: Path) -> bool:
                attempt_session.last_attempt_failure = None
                try:
                    with workspace_context(operation_root):
                        outcome = invoke_resolution_agent(
                            agent_name=agent,
                            prompt_path=prompt,
                            config=ctx.config,
                            pipeline_deps=pipeline_deps,
                            workspace_scope=WorkspaceScope(operation_root),
                            policy_bundle=ctx.policy_bundle,
                            display=ctx.active_display,
                            display_context=ctx.display_context,
                            require_completion_evidence=True,
                            session=attempt_session,
                        )
                        return outcome.succeeded
                except WorkspaceContextError as exc:
                    reason_text = exc.fix
                    attempt_session.last_attempt_failure = reason_text
                    attempt_session.last_failure_cause = ResolutionFailureCause.CONFIG_FAILURE
                    raise

            effective_invoke = _default_invoke
        else:
            effective_invoke = invoke

        try:
            col = collector if collector is not None else operation_evidence_collector()
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
                ledger=ledger,
                git=git,
                fs=fs,
                env=env,
                clock=clock,
            )
            retained = read_record(ctx.workspace_scope.root)
            return bool(
                retained is not None
                and identity_fingerprint(retained) == identity_fingerprint(record)
                and released
                and not retained.diagnostic_evidence
                and retained.diagnostic_ownership is None
            )
        except WorkspaceContextError as exc:
            _handle_diagnostic_config_failure(ctx.workspace_scope.root, record, exc)
        except Exception as exc:
            _handle_diagnostic_generic_failure(
                ctx.workspace_scope.root,
                record,
                exc,
                ledger=ledger,
                git=git,
                fs=fs,
                env=env,
                clock=clock,
            )
        return False


def _pre_pass_failure(
    root: Path,
    record: IntegrationRecord,
    backend: FileBackend,
    reason: str,
    *,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
    terminal_for_now: bool = False,
) -> bool:
    if ladder_terminal_state(root, record, ledger=ledger, git=git) is not None:
        logger.info(
            "Integration diagnostic: the cleanup ladder already ended in a state that "
            "needs an operator; the retained integration starts no new diagnostic round"
        )
        return False
    if terminal_for_now:
        from ralph.pipeline.auto_integrate_recovery_terminal import (
            RESUME_COMMAND,
            ResolutionTerminalMarker,
            resolution_terminal_marker,
        )

        marker = resolution_terminal_marker(record)
        if marker is not None and record.repair_last_error == reason:
            return False
        target_record = _bump_attempt(record, reason, repair_attempt_cap=repair_attempt_cap)
        terminal_marker = ResolutionTerminalMarker(
            record.resolution_gate_key,
            "LandedProofFailed",
            RESUME_COMMAND,
        )
        target_record = target_record.with_resolution_terminal_marker(
            terminal_marker.as_record_value()
        )
        write_record(root, target_record, backend=backend)
        logger.warning("Integration ladder terminal state: LandedProofFailed ({})", reason)
        return False
    target_record = _bump_attempt(record, reason, repair_attempt_cap=repair_attempt_cap)
    write_record(root, target_record, backend=backend)
    if target_record.merge_commit_repair_attempts >= repair_attempt_cap:
        try:
            escalate_diagnostic_budget_to_ladder(
                root,
                target_record,
                reason=reason,
                ledger=ledger,
                git=git,
                fs=fs,
                env=env,
                clock=clock,
                target=record.target,
            )
        except Exception:
            terminal_emitter.critical(
                "Integration diagnostic budget spent but ladder escalation raised; "
                "the durable ledger keeps the climb and the next seam resumes it",
            )
    logger.warning("Operation completion proof failed ({}); bounded diagnostic retry", reason)
    return False


def _run_completion_pre_pass(
    *,
    root: Path,
    inspection_root: Path,
    record: IntegrationRecord,
    snapshot: tuple[OperationFact, ...],
    next_action: IntegrationAction | RecordFactOutcome,
    backend: FileBackend,
    collector: OperationEvidenceCollector | None = None,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
) -> bool | None:
    if isinstance(next_action, RecordFactOutcome):
        try:
            step = escalate_record_fact_outcome(
                root,
                record,
                next_action,
                reason=last_outcome_reason(record) or next_action.value,
                ledger=ledger,
                git=git,
                fs=fs,
                env=env,
                clock=clock,
                target=record.target,
            )
        except Exception:
            terminal_emitter.critical(
                "Typed record-fact outcome {} could not escalate to the ladder; "
                "the durable ledger keeps the climb and the next seam resumes it",
                next_action.value,
            )
            return False
        if step.outcome == "terminal":
            logger.info(
                "Integration diagnostic: the cleanup ladder ended in a state that needs "
                "an operator ({}); the retained integration waits and starts no new "
                "diagnostic round",
                step.reason,
            )
        return False

    for fact in snapshot:
        if fact.kind in (
            _KIND_MERGE_OBJECT_RESOLVABLE,
            _KIND_REBASE_OBJECT_RESOLVABLE,
        ) and fact.value in ("False", "false"):
            return _pre_pass_failure(
                root,
                record,
                backend,
                "unreachable marker object; diagnostic retry budgeted",
                repair_attempt_cap=repair_attempt_cap,
                ledger=ledger,
                git=git,
                fs=fs,
                env=env,
                clock=clock,
            )

    if _completion_proof_applies(record):
        promoted_record, promotion_note = _promote_completed_rebase_before_proof(
            root,
            inspection_root,
            record,
            backend,
        )
        return _complete_or_intervene(
            root=root,
            inspection_root=inspection_root,
            record=promoted_record,
            backend=backend,
            collector=collector,
            repair_attempt_cap=repair_attempt_cap,
            ledger=ledger,
            git=git,
            fs=fs,
            env=env,
            clock=clock,
            promotion_note=promotion_note,
        )

    return None


def _promote_completed_rebase_before_proof(
    root: Path,
    inspection_root: Path,
    record: IntegrationRecord,
    backend: FileBackend,
) -> tuple[IntegrationRecord, str]:
    if record.operation_kind != "feature_integrate" or inspection_root != root:
        return record, ""
    from ralph.pipeline._pending_rebase_continue import promote_completed_pending_rebase

    promoted = promote_completed_pending_rebase(root, record, backend=backend)
    if isinstance(promoted, str):
        return record, promoted
    return promoted, ""


def _complete_or_intervene(
    *,
    root: Path,
    inspection_root: Path,
    record: IntegrationRecord,
    backend: FileBackend,
    collector: OperationEvidenceCollector | None,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
    promotion_note: str = "",
) -> bool:
    proof_ok, proof_reason = _check_operation_completion_proof(
        inspection_root, record, collector=collector
    )
    if not proof_ok:
        reason = f"operation completion proof failed: {proof_reason}"
        if promotion_note:
            reason = f"{reason}; {promotion_note}"
        return _pre_pass_failure(
            root,
            record,
            backend,
            reason,
            repair_attempt_cap=repair_attempt_cap,
            ledger=ledger,
            git=git,
            fs=fs,
            env=env,
            clock=clock,
            terminal_for_now=True,
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
        logger.warning("Release busy; record bytes preserved for the bounded-retry authority")
        return False
    except RecordUnreadableError:
        terminal_emitter.critical(
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
    *,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
) -> bool:
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
    target_record = budget_spent_record(record, repair_attempt_cap=repair_attempt_cap)
    write_record(root, target_record, backend=backend)
    try:
        escalate_diagnostic_budget_to_ladder(
            root,
            target_record,
            ledger=ledger,
            git=git,
            fs=fs,
            env=env,
            clock=clock,
            target=record.target,
        )
    except Exception:
        terminal_emitter.critical(
            "Integration diagnostic budget spent but ladder escalation raised; "
            "the durable ledger keeps the climb and the next seam resumes it",
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
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
) -> bool:
    inspection_root = _operation_root_for(record, root)
    terminal = ladder_terminal_state(root, record, ledger=ledger, git=git)
    if terminal is not None:
        pre_pass_result: bool | None = False
    else:
        pre_pass_result = None
    col = collector if collector is not None else operation_evidence_collector()

    if pre_pass_result is None:
        snapshot = col.snapshot(inspection_root, record)
        next_action = decide_seam_action(root, record=record)
        pre_pass_result = _run_completion_pre_pass(
            root=root,
            inspection_root=inspection_root,
            record=record,
            snapshot=snapshot,
            next_action=next_action,
            backend=backend,
            collector=col,
            repair_attempt_cap=repair_attempt_cap,
            ledger=ledger,
            git=git,
            fs=fs,
            env=env,
            clock=clock,
        )
    if pre_pass_result is not None:
        return pre_pass_result

    if record.merge_commit_repair_attempts >= repair_attempt_cap:
        return _settle_exhausted_cap(
            root,
            inspection_root,
            record,
            col,
            backend,
            repair_attempt_cap=repair_attempt_cap,
            ledger=ledger,
            git=git,
            fs=fs,
            env=env,
            clock=clock,
        )
    if not agents:
        target_record = _bump_attempt(
            record,
            "no eligible diagnostic agent available",
            repair_attempt_cap=repair_attempt_cap,
        )
        write_record(root, target_record, backend=backend)
        if target_record.merge_commit_repair_attempts >= repair_attempt_cap:
            try:
                escalate_diagnostic_budget_to_ladder(
                    root,
                    target_record,
                    ledger=ledger,
                    git=git,
                    fs=fs,
                    env=env,
                    clock=clock,
                    target=record.target,
                )
            except Exception:
                terminal_emitter.critical(
                    "Integration diagnostic budget spent but ladder escalation raised; "
                    "the durable ledger keeps the climb and the next seam resumes it",
                )
        return False
    agent = next_resolver(controller, agents, target_root=inspection_root)
    if agent is None:
        return False
    if agent not in agents:
        logger.warning(
            "Integration diagnostic: target worktree configuration error: {}",
            agent,
        )
        target_record = record.with_transition(
            BUMP_DIAGNOSTIC_ATTEMPT,
            repair_last_error=agent,
        ).model_copy(update={"last_failure_cause": ResolutionFailureCause.CONFIG_FAILURE})
        write_record(root, target_record, backend=backend)
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
        record.with_transition(BUMP_DIAGNOSTIC_ATTEMPT, repair_last_error=failure[:8192]),
        backend=backend,
    )
    return _invoke_and_judge(
        agent, prompt, invoke, controller, inspection_root, record, col, failure_detail
    )
