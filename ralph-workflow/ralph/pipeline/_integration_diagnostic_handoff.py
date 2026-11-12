from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

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
from ralph.pipeline.integration_decision import CleanupLadderRung, cleanup_action_for
from ralph.pipeline.integration_ladder import (
    LadderStepResult,
    default_git_plumbing,
    ensure_preserved_copy,
    preserved_copy_identity,
    record_salvage_attempt,
    resume_rung,
    run_ladder_rung,
    terminal_operator_message,
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
    from ralph.pipeline.integration_attempt_ledger import AttemptLedgerState
    from ralph.pipeline.operation_evidence import OperationEvidenceCollector
    from ralph.recovery.controller import RecoveryController

__all__ = [
    "budget_spent_record",
    "check_target_worktree_context",
    "escalate_diagnostic_budget_to_ladder",
    "escalate_record_fact_outcome",
    "ladder_terminal_state",
    "next_resolver",
    "observe_workspace_change",
]

_INTEGRATION_PROBE_KEY = "integration_resolution"

_LAST_RESORT_EVIDENCE_KEY: Final[str] = (
    f"rung:{cleanup_action_for(CleanupLadderRung.LAST_RESORT).value}"
)

_TERMINAL_EVIDENCE_PREFIX: Final[str] = "terminal:"

_TERMINAL_FACTS_EVIDENCE_KEY: Final[str] = "terminal:ladder_facts"


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


def _last_charged_signature(state: AttemptLedgerState) -> str:
    for signature, _action in reversed(list(state.repeat_counts)):
        return signature
    return ""


def _observed_signature(root: Path) -> str:
    from ralph.pipeline.integration_decision import AttemptState
    from ralph.pipeline.integration_director import observe

    try:
        return AttemptState().signature(observe(root))
    except Exception as exc:
        logger.warning("Integration diagnostic: blocking signature unreadable: {}", exc)
        return ""


def _resolve_blocking_signature(
    root: Path,
    state: AttemptLedgerState,
    rung: CleanupLadderRung,
    decided_signature: str | None,
    fresh_signature: str | None,
) -> tuple[str, str]:
    if decided_signature is not None:
        return decided_signature, fresh_signature or ""
    if rung is not CleanupLadderRung.LAST_RESORT:
        return "", ""
    charged = _last_charged_signature(state)
    if not charged:
        return "", ""
    observed = _observed_signature(root)
    if observed and observed == charged:
        return charged, observed
    return "", ""


def _preserved_copy_clause(
    root: Path,
    *,
    step: LadderStepResult,
    ladder: AttemptLedger,
    plumbing: GitPlumbing,
    fs: FileQuarantine | None,
    state_dir: Path | None,
    observed_head: str | None,
) -> str:
    identity = preserved_copy_identity(step, ladder.read())
    if identity:
        return identity
    if not plumbing.changed_paths(root):
        return f"nothing to preserve at HEAD {observed_head or 'unreadable'}"
    preserved = ensure_preserved_copy(
        root,
        ledger=ladder,
        git=plumbing,
        fs=fs,
        state_dir=state_dir,
    )
    if preserved is not None:
        identity = preserved_copy_identity(preserved, ladder.read())
        if identity:
            return identity
    return "the uncommitted changes are not preserved in any durable copy"


def _ladder_facts_fingerprint(record: IntegrationRecord, observed_head: str | None) -> str:
    return "|".join(
        (
            observed_head or "unreadable",
            record.integrated_feature_sha or "unset",
            record.pre_target_sha or "unset",
            record.pre_feature_sha or "unset",
            record.rebase_continue_stop or "unset",
            record.rebase_continue_head or "unset",
            record.rebase_continue_tree or "unset",
            record.merge_commit_parent or "unset",
        )
    )


def _record_ladder_facts(
    ledger: AttemptLedger,
    record: IntegrationRecord,
    observed_head: str | None,
) -> None:
    facts = _ladder_facts_fingerprint(record, observed_head)
    state = ledger.read()
    if state.ladder_evidence.get(_TERMINAL_FACTS_EVIDENCE_KEY) == facts:
        return
    ledger.persist(
        replace(
            state,
            ladder_evidence={**state.ladder_evidence, _TERMINAL_FACTS_EVIDENCE_KEY: facts},
        )
    )


def ladder_terminal_state(
    root: Path,
    record: IntegrationRecord,
    *,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
) -> LadderStepResult | None:
    ladder = ledger if ledger is not None else AttemptLedger(root)
    state = ladder.read()
    value = state.ladder_evidence.get(_LAST_RESORT_EVIDENCE_KEY)
    if value is None or not value.startswith(_TERMINAL_EVIDENCE_PREFIX):
        return None
    recorded = state.ladder_evidence.get(_TERMINAL_FACTS_EVIDENCE_KEY)
    if recorded is not None:
        plumbing = git if git is not None else default_git_plumbing()
        if recorded != _ladder_facts_fingerprint(record, plumbing.head_sha(root)):
            return None
    return LadderStepResult(
        CleanupLadderRung.LAST_RESORT,
        "terminal",
        value,
        evidence={_LAST_RESORT_EVIDENCE_KEY: value},
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
    decided_signature: str | None = None,
    fresh_signature: str | None = None,
) -> LadderStepResult:
    ladder = ledger if ledger is not None else AttemptLedger(root)
    plumbing = git if git is not None else default_git_plumbing()
    terminal = ladder_terminal_state(root, record, ledger=ladder, git=plumbing)
    if terminal is not None:
        return terminal
    if outcome is RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT:
        record_salvage_attempt(ladder, "resolver_agent", f"failed:{reason}")
    rung = resume_rung(ladder.read())
    if rung is None:
        rung = CleanupLadderRung.LAST_RESORT
    decided, fresh = _resolve_blocking_signature(
        root,
        ladder.read(),
        rung,
        decided_signature,
        fresh_signature,
    )
    step = run_ladder_rung(
        rung,
        root=root,
        ledger=ladder,
        git=plumbing,
        fs=fs,
        env=env,
        clock=clock,
        state_dir=state_dir,
        target=record.target if target is None else target,
        branch=branch,
        feature_tip=feature_tip,
        decided_signature=decided,
        fresh_signature=fresh,
        terminal_when_disabled=True,
    )
    observed_head = plumbing.head_sha(root)
    if step.outcome == "terminal":
        _record_ladder_facts(ladder, record, observed_head)
    preserved = _preserved_copy_clause(
        root,
        step=step,
        ladder=ladder,
        plumbing=plumbing,
        fs=fs,
        state_dir=state_dir,
        observed_head=observed_head,
    )
    if step.outcome == "terminal":
        message = terminal_operator_message(
            record,
            reason=step.reason,
            observed_head=observed_head,
            preserved_copy=preserved,
        )
    else:
        message = (
            "Integration diagnostic budget spent; escalated autonomously to fallback "
            f"ladder rung {rung.value} ({step.outcome}): {step.reason}; "
            f"preserved copy: {preserved}; the run continues without waiting on a person"
        )
    logger.warning("{}", message)
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
