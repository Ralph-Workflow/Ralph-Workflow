from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
)
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.integration_director import (
    DEFAULT_REMOTE,
    DEFAULT_TARGET_BRANCH,
)
from ralph.pipeline.integration_owner import RecordFactOutcome

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy


class PhaseDecision(StrEnum):
    CONTINUE_PHASE = "continue_phase"
    ADVANCE_PHASE = "advance_phase"
    BLOCK_FOR_RECOVERY = "block_for_recovery"
    REPORT_TERMINAL_SUCCESS = "report_terminal_success"
    REPORT_TERMINAL_FAILURE = "report_terminal_failure"
    REPORT_INTERVENTION_REQUIRED = "report_intervention_required"


_ALL_DECISIONS: frozenset[str] = frozenset(d.value for d in PhaseDecision)

_BLOCKING_ACTIONS: frozenset[IntegrationAction] = frozenset(
    {
        IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        IntegrationAction.CONTINUE_GIT_OPERATION,
        IntegrationAction.REINTEGRATE_AGAINST_TARGET,
        IntegrationAction.LAND_TARGET,
        IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
    }
)


def _configured_target(config: UnifiedConfig | None) -> str:
    if config is None:
        return DEFAULT_TARGET_BRANCH
    return config.general.auto_integrate_target


def _configured_remote(config: UnifiedConfig | None) -> str:
    if config is None:
        return DEFAULT_REMOTE
    return config.general.auto_integrate_remote


def _owner_action(
    root: Path | None,
    record: IntegrationRecord,
    *,
    config: UnifiedConfig | None,
    agent_active: bool,
) -> IntegrationAction | RecordFactOutcome:
    if root is None:
        return IntegrationAction.RESOLVE_ACTIVE_OPERATION
    action = decide_seam_action(
        root,
        record=record,
        agent_active=agent_active,
        ledger_path=None,
        target=_configured_target(config),
        remote=_configured_remote(config),
    )
    return action


def _check_blocking_decision(
    integration_record: IntegrationRecord | None,
    rebase_state: RebaseState,
    *,
    root: Path | None,
    config: UnifiedConfig | None,
    agent_active: bool,
) -> PhaseDecision | None:
    if integration_record is not None:
        action = _owner_action(
            root,
            integration_record,
            config=config,
            agent_active=agent_active,
        )
        if action in _BLOCKING_ACTIONS or isinstance(action, RecordFactOutcome):
            return PhaseDecision.BLOCK_FOR_RECOVERY
    if rebase_state.is_intervention_required is True:
        if rebase_state.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION:
            return PhaseDecision.BLOCK_FOR_RECOVERY
        return PhaseDecision.REPORT_INTERVENTION_REQUIRED
    last_outcome = rebase_state.last_outcome
    fast_forwarded = rebase_state.fast_forwarded
    unlanded_rebase = (
        last_outcome in (RebaseOutcomeKind.REBASED, RebaseOutcomeKind.MERGED)
        and fast_forwarded is False
    )
    if (
        unlanded_rebase
        or rebase_state.integration_unresolved is True
        or rebase_state.recovery_record_retained is True
    ):
        return PhaseDecision.BLOCK_FOR_RECOVERY
    return None


def _is_terminal_failure(phase: str, policy: PipelinePolicy | None) -> bool:
    if policy is None:
        return False
    try:
        from ralph.pipeline.reducer import _terminal_failure_route, _terminal_success_route

        failure_route = _terminal_failure_route(policy)
        success_route = _terminal_success_route(policy)
        if failure_route != success_route:
            return phase == failure_route
        return False
    except Exception:
        return False


def decide_phase_transition(
    state: PipelineState,
    policy: PipelinePolicy | None = None,
    *,
    integration_record: IntegrationRecord | None,
    rebase_state: RebaseState,
    root: Path | None = None,
    config: UnifiedConfig | None = None,
    agent_active: bool = False,
) -> PhaseDecision:
    blocking = _check_blocking_decision(
        integration_record,
        rebase_state,
        root=root,
        config=config,
        agent_active=agent_active,
    )
    if blocking is not None:
        return blocking
    if policy is None:
        return PhaseDecision.CONTINUE_PHASE

    terminal: set[str] = set()
    if hasattr(policy, "terminal_states"):
        terminal.update(policy.terminal_states())
    if hasattr(policy, "terminal_phase") and policy.terminal_phase:
        terminal.add(policy.terminal_phase)

    from ralph.pipeline.handoffs import resolve_next_phase

    candidate_phase: str | None = None
    try:
        candidate_phase = resolve_next_phase(state.phase, "success", policy)
    except ValueError:
        if state.phase in terminal:
            candidate_phase = state.phase

    decision: PhaseDecision
    if candidate_phase is None:
        decision = PhaseDecision.REPORT_TERMINAL_FAILURE
    elif candidate_phase in terminal:
        if _is_terminal_failure(candidate_phase, policy):
            decision = PhaseDecision.REPORT_TERMINAL_FAILURE
        elif not is_terminal_success_eligible(state, integration_record, rebase_state):
            decision = PhaseDecision.BLOCK_FOR_RECOVERY
        else:
            decision = PhaseDecision.REPORT_TERMINAL_SUCCESS
    elif candidate_phase == state.phase:
        decision = PhaseDecision.CONTINUE_PHASE
    else:
        decision = PhaseDecision.ADVANCE_PHASE

    return decision


__all__ = [
    "PhaseDecision",
    "decide_phase_transition",
    "is_terminal_success_eligible",
]
