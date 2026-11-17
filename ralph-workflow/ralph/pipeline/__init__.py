from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _load_event() -> object:
    from ralph.pipeline.events import Event
    return Event

def _load_integrationaction() -> object:
    from ralph.pipeline._integration_action import IntegrationAction
    return IntegrationAction

def _load_integrationpolicy() -> object:
    from ralph.pipeline.integration_strategy import IntegrationPolicy
    return IntegrationPolicy

def _load_integrationrelation() -> object:
    from ralph.pipeline.integration_strategy import IntegrationRelation
    return IntegrationRelation

def _load_integrationstatus() -> object:
    from ralph.pipeline._integration_status import IntegrationStatus
    return IntegrationStatus

def _load_integrationstrategy() -> object:
    from ralph.pipeline.integration_strategy import IntegrationStrategy
    return IntegrationStrategy

def _load_integrationworkshape() -> object:
    from ralph.pipeline.integration_strategy import IntegrationWorkShape
    return IntegrationWorkShape

def _load_observedintegration() -> object:
    from ralph.pipeline.integration_strategy import ObservedIntegration
    return ObservedIntegration

def _load_pipelineevent() -> object:
    from ralph.pipeline.events import PipelineEvent
    return PipelineEvent

def _load_pipelinephase() -> object:
    from ralph.config.enums import PipelinePhase
    return PipelinePhase

def _load_pipelinestate() -> object:
    from ralph.pipeline.state import PipelineState
    return PipelineState

def _load_planningsyncverdict() -> object:
    from ralph.pipeline._integration_owner_seam import PlanningSyncVerdict
    return PlanningSyncVerdict

def _load_rebaseoutcomekind() -> object:
    from ralph.pipeline._integration_action import RebaseOutcomeKind
    return RebaseOutcomeKind

def _load_rebaseresolutionresult() -> object:
    from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
    return RebaseResolutionResult

def _load_stopresolutionoutcome() -> object:
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome
    return StopResolutionOutcome

def _load_strategy_order() -> object:
    from ralph.pipeline.integration_strategy import STRATEGY_ORDER
    return STRATEGY_ORDER

def _load_strategy_transitions() -> object:
    from ralph.pipeline.integration_strategy import STRATEGY_TRANSITIONS
    return STRATEGY_TRANSITIONS

def _load_stopresolutiontrigger() -> object:
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
    return StopResolutionTrigger

def _load_strategydecision() -> object:
    from ralph.pipeline.integration_strategy import StrategyDecision
    return StrategyDecision

def _load_strategytransition() -> object:
    from ralph.pipeline.integration_strategy import StrategyTransition
    return StrategyTransition

def _load_strategytransitionguard() -> object:
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
    "Event": _load_event,
    "IntegrationAction": _load_integrationaction,
    "IntegrationPolicy": _load_integrationpolicy,
    "IntegrationRelation": _load_integrationrelation,
    "IntegrationStatus": _load_integrationstatus,
    "IntegrationStrategy": _load_integrationstrategy,
    "IntegrationWorkShape": _load_integrationworkshape,
    "ObservedIntegration": _load_observedintegration,
    "PipelineEvent": _load_pipelineevent,
    "PipelinePhase": _load_pipelinephase,
    "PipelineState": _load_pipelinestate,
    "PlanningSyncVerdict": _load_planningsyncverdict,
    "RebaseOutcomeKind": _load_rebaseoutcomekind,
    "RebaseResolutionResult": _load_rebaseresolutionresult,
    "StopResolutionOutcome": _load_stopresolutionoutcome,
    "STRATEGY_ORDER": _load_strategy_order,
    "STRATEGY_TRANSITIONS": _load_strategy_transitions,
    "StopResolutionTrigger": _load_stopresolutiontrigger,
    "StrategyDecision": _load_strategydecision,
    "StrategyTransition": _load_strategytransition,
    "StrategyTransitionGuard": _load_strategytransitionguard,
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
