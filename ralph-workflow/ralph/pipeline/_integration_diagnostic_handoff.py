from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline._integration_diagnostic_state import (
    DIAGNOSTIC_BUDGET_SPENT_REASON,
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
)
from ralph.pipeline.auto_integrate_record import BUMP_DIAGNOSTIC_ATTEMPT
from ralph.pipeline.conflict_resolution.resolver_health import (
    bench_resolver,
    choose_resolver,
    clear_resolver,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import CleanupLadderRung
from ralph.pipeline.integration_ladder import (
    LadderStepResult,
    record_salvage_attempt,
    resume_rung,
    run_ladder_rung,
)
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.pipeline._integration_ladder_seams import (
        EnvironmentProbe,
        FileQuarantine,
        GitPlumbing,
    )
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_actions import Clock
    from ralph.pipeline.operation_evidence import OperationEvidenceCollector
    from ralph.recovery.controller import RecoveryController

__all__ = [
    "budget_spent_record",
    "check_target_worktree_context",
    "escalate_diagnostic_budget_to_ladder",
    "escalate_record_fact_outcome",
    "next_resolver",
    "observe_workspace_change",
]

_INTEGRATION_PROBE_KEY = "integration_resolution"


def observe_workspace_change(
    controller: RecoveryController,
    root: Path,
    attempt: Callable[[], None],
) -> bool:
    probe = controller.workspace_probe
    if probe is None:
        attempt()
        return False
    from ralph.workspace.fs import FsWorkspace

    workspace = FsWorkspace(root)
    try:
        probe.observe_before(_INTEGRATION_PROBE_KEY, workspace)
    except Exception as exc:
        logger.warning(
            "Integration: workspace baseline could not be observed before the attempt: {}", exc
        )
    attempt()
    try:
        return bool(probe.has_changed(_INTEGRATION_PROBE_KEY, workspace))
    except Exception as exc:
        logger.warning(
            "Integration: workspace change could not be observed after the attempt: {}", exc
        )
        return False


def check_target_worktree_context(target_root: Path) -> str | None:
    from ralph.workspace.context import WorkspaceContextError, workspace_context

    try:
        with workspace_context(target_root):
            pass
    except WorkspaceContextError as exc:
        return exc.fix
    except Exception as exc:
        return f"target worktree context error: {exc}"
    return None


def next_resolver(
    controller: RecoveryController,
    agents: Sequence[str],
    target_root: Path | None = None,
) -> str | None:
    if target_root is not None:
        failure_reason = check_target_worktree_context(target_root)
        if failure_reason is not None:
            return failure_reason

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
    from ralph.pipeline._integration_diagnostic_proof import _check_operation_completion_proof
    from ralph.workspace.context import WorkspaceContextError

    def _attempt() -> None:
        invoke(agent, prompt)

    try:
        workspace_changed = observe_workspace_change(controller, inspection_root, _attempt)
    except WorkspaceContextError:
        raise
    except Exception as exc:
        bench_resolver(controller, agent, exc)
        raise
    verified, _reason = _check_operation_completion_proof(inspection_root, record, collector=col)
    if verified:
        clear_resolver(controller, agent)
    else:
        detail = failure_detail() if failure_detail is not None else None
        if detail is not None:
            bench_resolver(
                controller,
                agent,
                detail,
                workspace_changed=workspace_changed,
            )
        else:
            bench_resolver(
                controller,
                agent,
                None,
                workspace_changed=workspace_changed,
                outcome=ConflictAttemptOutcome.ATTEMPT_FAILED,
            )
    return verified


def budget_spent_record(
    record: IntegrationRecord,
    *,
    reason: str = DIAGNOSTIC_BUDGET_SPENT_REASON,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
) -> IntegrationRecord:

    return record.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        merge_commit_repair_attempts=min(
            record.merge_commit_repair_attempts + 1, repair_attempt_cap
        ),
        repair_last_error=reason,
        repair_cap_reason=reason,
    )


def escalate_record_fact_outcome(
    root: Path,
    record: IntegrationRecord,
    outcome: RecordFactOutcome,
    *,
    reason: str,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
    state_dir: Path | None = None,
    target: str | None = None,
    branch: str = "feature",
    feature_tip: str | None = None,
) -> LadderStepResult:
    ladder = ledger if ledger is not None else AttemptLedger(root)
    if outcome is RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT:
        record_salvage_attempt(ladder, "resolver_agent", f"failed:{reason}")
    rung = resume_rung(ladder.read())
    if rung is None:
        rung = CleanupLadderRung.LAST_RESORT
    step = run_ladder_rung(
        rung,
        root=root,
        ledger=ladder,
        git=git,
        fs=fs,
        env=env,
        clock=clock,
        state_dir=state_dir,
        target=record.target if target is None else target,
        branch=branch,
        feature_tip=feature_tip,
    )
    preserved = step.preserved_ref or step.preserved_sidecar or "no preserved copy yet"
    logger.warning(
        "Integration diagnostic budget spent; escalated autonomously to fallback "
        "ladder rung {} ({}): {}; preserved copy: {}; the run continues without "
        "waiting on a person",
        rung.value,
        step.outcome,
        step.reason,
        preserved,
    )
    return step


def escalate_diagnostic_budget_to_ladder(
    root: Path,
    record: IntegrationRecord,
    *,
    reason: str = DIAGNOSTIC_BUDGET_SPENT_REASON,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
    state_dir: Path | None = None,
    target: str | None = None,
    branch: str = "feature",
    feature_tip: str | None = None,
) -> LadderStepResult:
    return escalate_record_fact_outcome(
        root,
        record,
        RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
        reason=reason,
        ledger=ledger,
        git=git,
        fs=fs,
        env=env,
        clock=clock,
        state_dir=state_dir,
        target=target,
        branch=branch,
        feature_tip=feature_tip,
    )
