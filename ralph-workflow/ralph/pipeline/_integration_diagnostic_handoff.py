"""Diagnostic handoff: offer the retained-integration inspection to a resolver.

The handoff is the only place that calls a diagnostic agent. It owns the
repair-attempt budget, the completion pre-pass (which may release the held
evidence without calling any agent at all) and the resolver-health bookkeeping
around a single invocation.

Split out of ``ralph.pipeline._integration_diagnostic`` to stay within the
repo-structure line limits; the public surface is unchanged and re-exported
from there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline._integration_action import (
    IntegrationAction,
    last_outcome_reason,
)
from ralph.pipeline._integration_diagnostic_state import (
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    _operation_root_for,
    operation_evidence_collector,
)
from ralph.pipeline._integration_record_facts import apply_record_facts
from ralph.pipeline.auto_integrate_record import (
    RELEASE_DIAGNOSTIC_EVIDENCE,
    RecordBusyError,
    RecordUnreadableError,
    write_record,
)
from ralph.pipeline.auto_integrate_record_store import apply_record_transition
from ralph.pipeline.conflict_resolution.resolver_health import (
    bench_resolver,
    choose_resolver,
    clear_resolver,
)
from ralph.pipeline.integration_owner import decide_integration
from ralph.pipeline.operation_fact import (
    _KIND_MERGE_OBJECT_RESOLVABLE,
    _KIND_REBASE_OBJECT_RESOLVABLE,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.operation_evidence import OperationEvidenceCollector, OperationFact
    from ralph.recovery.controller import RecoveryController


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
    from ralph.pipeline._integration_diagnostic_proof import _check_operation_completion_proof

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


def _pre_pass_intervention(
    root: Path,
    record: IntegrationRecord,
    backend: FileBackend,
    reason: str,
) -> bool:
    from ralph.pipeline._integration_diagnostic import _raise_intervention

    intervention = _raise_intervention(record, reason)
    write_record(root, intervention, backend=backend)
    logger.critical(
        "Operation completion proof failed ({}); operator intervention required",
        reason,
    )
    return False


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

    if next_action in (
        IntegrationAction.CONTINUE_GIT_OPERATION,
        IntegrationAction.LAND_TARGET,
    ):
        return _complete_or_intervene(
            root=root,
            inspection_root=inspection_root,
            record=record,
            backend=backend,
            collector=collector,
        )

    if next_action == IntegrationAction.REQUIRE_INTERVENTION:
        reason = last_outcome_reason(record) or "operator intervention required"
        return _pre_pass_intervention(root, record, backend, reason)

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
    from ralph.pipeline._integration_diagnostic_proof import _check_operation_completion_proof

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
        # ``RebaseState.retry_deadline`` (see :func:`busy_refusal_outcome`);
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
    from ralph.pipeline._integration_diagnostic import (
        _raise_intervention,
        diagnostic_needed,
    )
    from ralph.pipeline._integration_diagnostic_proof import (
        _check_operation_completion_proof,
    )

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
    from ralph.pipeline._integration_diagnostic import _bump_attempt

    inspection_root = _operation_root_for(record, root)
    col = collector if collector is not None else operation_evidence_collector()
    snapshot = col.snapshot(inspection_root, record)
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
