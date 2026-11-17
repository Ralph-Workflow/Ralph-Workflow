from __future__ import annotations

from collections.abc import Callable

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
    from ralph.pipeline._auto_integrate_rebase_resolve import (
        resolve_conflicted_rebase,
    )
    from ralph.pipeline._integration_action import IntegrationAction, RebaseOutcomeKind
    from ralph.pipeline._integration_diagnostic_handoff import escalate_record_fact_outcome
    from ralph.pipeline._integration_owner_seam import (
        PlanningSyncVerdict,
        decide_seam_action,
    )
    from ralph.pipeline._integration_status import (
        IntegrationStatus,
        build_integration_status,
    )
    from ralph.pipeline._runner_session import apply_session_capture
    from ralph.pipeline._stop_resolution_entry import (
        adopt_resolving_rebase_record,
        attempt_stop_resolution,
    )
    from ralph.pipeline.events import Event, PipelineEvent
    from ralph.pipeline.integration_strategy import (
        IntegrationPolicy,
        IntegrationRelation,
        IntegrationStrategy,
        IntegrationWorkShape,
        ObservedIntegration,
        StrategyDecision,
        StrategyTransition,
        StrategyTransitionGuard,
    )
    from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
    from ralph.pipeline.reducer import reduce
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome
    from ralph.pipeline.state import PipelineState
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

__all__ = [
    "STRATEGY_ORDER",
    "STRATEGY_TRANSITIONS",
    "Event",
    "IntegrationAction",
    "IntegrationPolicy",
    "IntegrationRelation",
    "IntegrationStatus",
    "IntegrationStrategy",
    "IntegrationWorkShape",
    "ObservedIntegration",
    "PipelineEvent",
    "PipelinePhase",
    "PipelineState",
    "PlanningSyncVerdict",
    "RebaseOutcomeKind",
    "RebaseResolutionResult",
    "StopResolutionOutcome",
    "StopResolutionTrigger",
    "StrategyDecision",
    "StrategyTransition",
    "StrategyTransitionGuard",
    "adopt_resolving_rebase_record",
    "apply_session_capture",
    "attempt_stop_resolution",
    "build_integration_status",
    "choose_integration_strategy",
    "decide_seam_action",
    "escalate_record_fact_outcome",
    "integration_action",
    "integration_owner_seam",
    "recover_target_resolution",
    "reduce",
    "resolve_conflicted_rebase",
]


def _load_Event() -> object:
    from ralph.pipeline.events import Event
    return Event

def _load_IntegrationAction() -> object:
    from ralph.pipeline._integration_action import IntegrationAction
    return IntegrationAction

def _load_IntegrationPolicy() -> object:
    from ralph.pipeline.integration_strategy import IntegrationPolicy
    return IntegrationPolicy

def _load_IntegrationRelation() -> object:
    from ralph.pipeline.integration_strategy import IntegrationRelation
    return IntegrationRelation

def _load_IntegrationStatus() -> object:
    from ralph.pipeline._integration_status import IntegrationStatus
    return IntegrationStatus

def _load_IntegrationStrategy() -> object:
    from ralph.pipeline.integration_strategy import IntegrationStrategy
    return IntegrationStrategy

def _load_IntegrationWorkShape() -> object:
    from ralph.pipeline.integration_strategy import IntegrationWorkShape
    return IntegrationWorkShape

def _load_ObservedIntegration() -> object:
    from ralph.pipeline.integration_strategy import ObservedIntegration
    return ObservedIntegration

def _load_PipelineEvent() -> object:
    from ralph.pipeline.events import PipelineEvent
    return PipelineEvent

def _load_PipelinePhase() -> object:
    from ralph.config.enums import PipelinePhase
    return PipelinePhase

def _load_PipelineState() -> object:
    from ralph.pipeline.state import PipelineState
    return PipelineState

def _load_PlanningSyncVerdict() -> object:
    from ralph.pipeline._integration_owner_seam import PlanningSyncVerdict
    return PlanningSyncVerdict

def _load_RebaseOutcomeKind() -> object:
    from ralph.pipeline._integration_action import RebaseOutcomeKind
    return RebaseOutcomeKind

def _load_RebaseResolutionResult() -> object:
    from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
    return RebaseResolutionResult

def _load_StopResolutionOutcome() -> object:
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome
    return StopResolutionOutcome

def _load_STRATEGY_ORDER() -> object:
    from ralph.pipeline.integration_strategy import STRATEGY_ORDER
    return STRATEGY_ORDER

def _load_STRATEGY_TRANSITIONS() -> object:
    from ralph.pipeline.integration_strategy import STRATEGY_TRANSITIONS
    return STRATEGY_TRANSITIONS

def _load_StopResolutionTrigger() -> object:
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
    return StopResolutionTrigger

def _load_StrategyDecision() -> object:
    from ralph.pipeline.integration_strategy import StrategyDecision
    return StrategyDecision

def _load_StrategyTransition() -> object:
    from ralph.pipeline.integration_strategy import StrategyTransition
    return StrategyTransition

def _load_StrategyTransitionGuard() -> object:
    from ralph.pipeline.integration_strategy import StrategyTransitionGuard
    return StrategyTransitionGuard

def _load_apply_session_capture() -> object:
    from ralph.pipeline._runner_session import apply_session_capture
    return apply_session_capture

def _load_adopt_resolving_rebase_record() -> object:
    from ralph.pipeline._stop_resolution_entry import adopt_resolving_rebase_record
    return adopt_resolving_rebase_record

def _load_attempt_stop_resolution() -> object:
    from ralph.pipeline._stop_resolution_entry import attempt_stop_resolution
    return attempt_stop_resolution

def _load_build_integration_status() -> object:
    from ralph.pipeline._integration_status import build_integration_status
    return build_integration_status

def _load_choose_integration_strategy() -> object:
    from ralph.pipeline.integration_strategy import choose_integration_strategy
    return choose_integration_strategy

def _load_decide_seam_action() -> object:
    from ralph.pipeline._integration_owner_seam import decide_seam_action
    return decide_seam_action

def _load_escalate_record_fact_outcome() -> object:
    from ralph.pipeline._integration_diagnostic_handoff import escalate_record_fact_outcome
    return escalate_record_fact_outcome

def _load_recover_target_resolution() -> object:
    from ralph.pipeline._remote_reconcile_recovery import recover_target_resolution
    return recover_target_resolution

def _load_reduce() -> object:
    from ralph.pipeline.reducer import reduce
    return reduce

def _load_resolve_conflicted_rebase() -> object:
    from ralph.pipeline._auto_integrate_rebase_resolve import resolve_conflicted_rebase
    return resolve_conflicted_rebase

def _load_integration_action() -> object:
    from ralph.pipeline import _integration_action as _mod
    return _mod

def _load_integration_owner_seam() -> object:
    from ralph.pipeline import _integration_owner_seam as _mod
    return _mod

_LOADERS: dict[str, Callable[[], object]] = {
    "Event": _load_Event,
    "IntegrationAction": _load_IntegrationAction,
    "IntegrationPolicy": _load_IntegrationPolicy,
    "IntegrationRelation": _load_IntegrationRelation,
    "IntegrationStatus": _load_IntegrationStatus,
    "IntegrationStrategy": _load_IntegrationStrategy,
    "IntegrationWorkShape": _load_IntegrationWorkShape,
    "ObservedIntegration": _load_ObservedIntegration,
    "PipelineEvent": _load_PipelineEvent,
    "PipelinePhase": _load_PipelinePhase,
    "PipelineState": _load_PipelineState,
    "PlanningSyncVerdict": _load_PlanningSyncVerdict,
    "RebaseOutcomeKind": _load_RebaseOutcomeKind,
    "RebaseResolutionResult": _load_RebaseResolutionResult,
    "StopResolutionOutcome": _load_StopResolutionOutcome,
    "STRATEGY_ORDER": _load_STRATEGY_ORDER,
    "STRATEGY_TRANSITIONS": _load_STRATEGY_TRANSITIONS,
    "StopResolutionTrigger": _load_StopResolutionTrigger,
    "StrategyDecision": _load_StrategyDecision,
    "StrategyTransition": _load_StrategyTransition,
    "StrategyTransitionGuard": _load_StrategyTransitionGuard,
    "apply_session_capture": _load_apply_session_capture,
    "adopt_resolving_rebase_record": _load_adopt_resolving_rebase_record,
    "attempt_stop_resolution": _load_attempt_stop_resolution,
    "build_integration_status": _load_build_integration_status,
    "choose_integration_strategy": _load_choose_integration_strategy,
    "decide_seam_action": _load_decide_seam_action,
    "escalate_record_fact_outcome": _load_escalate_record_fact_outcome,
    "recover_target_resolution": _load_recover_target_resolution,
    "reduce": _load_reduce,
    "resolve_conflicted_rebase": _load_resolve_conflicted_rebase,
    "integration_action": _load_integration_action,
    "integration_owner_seam": _load_integration_owner_seam,
}


def __getattr__(name: str) -> object:
    loader = _LOADERS.get(name)
    if loader is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return loader()
