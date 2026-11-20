from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

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
    "cooldown_next_eligible",
    "decide_seam_action",
    "escalate_record_fact_outcome",
    "integration_action",
    "integration_owner_seam",
    "operation_landed",
    "recover_target_resolution",
    "reduce",
    "resolve_conflicted_rebase",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "Event": ("ralph.pipeline.events", "Event"),
    "IntegrationAction": ("ralph.pipeline._integration_action", "IntegrationAction"),
    "IntegrationPolicy": ("ralph.pipeline.integration_strategy", "IntegrationPolicy"),
    "IntegrationRelation": ("ralph.pipeline.integration_strategy", "IntegrationRelation"),
    "IntegrationStatus": ("ralph.pipeline._integration_status", "IntegrationStatus"),
    "IntegrationStrategy": ("ralph.pipeline.integration_strategy", "IntegrationStrategy"),
    "IntegrationWorkShape": ("ralph.pipeline.integration_strategy", "IntegrationWorkShape"),
    "ObservedIntegration": ("ralph.pipeline.integration_strategy", "ObservedIntegration"),
    "PipelineEvent": ("ralph.pipeline.events", "PipelineEvent"),
    "PipelinePhase": ("ralph.config.enums", "PipelinePhase"),
    "PipelineState": ("ralph.pipeline.state", "PipelineState"),
    "PlanningSyncVerdict": (
        "ralph.pipeline._integration_owner_seam",
        "PlanningSyncVerdict",
    ),
    "RebaseOutcomeKind": ("ralph.pipeline._integration_action", "RebaseOutcomeKind"),
    "RebaseResolutionResult": (
        "ralph.pipeline.rebase_resolution_result",
        "RebaseResolutionResult",
    ),
    "StopResolutionOutcome": (
        "ralph.pipeline.resolution_outcome",
        "StopResolutionOutcome",
    ),
    "STRATEGY_ORDER": ("ralph.pipeline.integration_strategy", "STRATEGY_ORDER"),
    "STRATEGY_TRANSITIONS": ("ralph.pipeline.integration_strategy", "STRATEGY_TRANSITIONS"),
    "StopResolutionTrigger": (
        "ralph.pipeline.stop_resolution_trigger",
        "StopResolutionTrigger",
    ),
    "StrategyDecision": ("ralph.pipeline.integration_strategy", "StrategyDecision"),
    "StrategyTransition": ("ralph.pipeline.integration_strategy", "StrategyTransition"),
    "StrategyTransitionGuard": (
        "ralph.pipeline.integration_strategy",
        "StrategyTransitionGuard",
    ),
    "apply_session_capture": ("ralph.pipeline._runner_session", "apply_session_capture"),
    "adopt_resolving_rebase_record": (
        "ralph.pipeline._stop_resolution_entry",
        "adopt_resolving_rebase_record",
    ),
    "attempt_stop_resolution": (
        "ralph.pipeline._stop_resolution_entry",
        "attempt_stop_resolution",
    ),
    "build_integration_status": (
        "ralph.pipeline._integration_status",
        "build_integration_status",
    ),
    "cooldown_next_eligible": (
        "ralph.pipeline._integration_status",
        "cooldown_next_eligible",
    ),
    "choose_integration_strategy": (
        "ralph.pipeline.integration_strategy",
        "choose_integration_strategy",
    ),
    "operation_landed": (
        "ralph.pipeline._landed_predicate",
        "operation_landed",
    ),
    "decide_seam_action": ("ralph.pipeline._integration_owner_seam", "decide_seam_action"),
    "escalate_record_fact_outcome": (
        "ralph.pipeline._integration_diagnostic_handoff",
        "escalate_record_fact_outcome",
    ),
    "recover_target_resolution": (
        "ralph.pipeline._remote_reconcile_recovery",
        "recover_target_resolution",
    ),
    "reduce": ("ralph.pipeline.reducer", "reduce"),
    "resolve_conflicted_rebase": (
        "ralph.pipeline._auto_integrate_rebase_resolve",
        "resolve_conflicted_rebase",
    ),
}

_LAZY_MODULE_EXPORTS: dict[str, str] = {
    "integration_action": "ralph.pipeline._integration_action",
    "integration_owner_seam": "ralph.pipeline._integration_owner_seam",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_MODULE_EXPORTS.get(name)
    if module_name is not None:
        module = import_module(module_name)
        cast("dict[str, object]", globals())[name] = module
        return module

    try:
        defining_module, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from exc

    module = import_module(defining_module)
    namespace = cast("dict[str, object]", module.__dict__)
    value = namespace[attr_name]
    cast("dict[str, object]", globals())[name] = value
    return value
