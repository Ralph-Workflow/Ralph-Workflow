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
    from ralph.pipeline._stop_resolution_entry import attempt_stop_resolution
    from ralph.pipeline.events import Event, PipelineEvent
    from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
    from ralph.pipeline.reducer import reduce
    from ralph.pipeline.state import PipelineState
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

__all__ = [
    "Event",
    "IntegrationAction",
    "IntegrationStatus",
    "PipelineEvent",
    "PipelinePhase",
    "PipelineState",
    "PlanningSyncVerdict",
    "RebaseOutcomeKind",
    "RebaseResolutionResult",
    "StopResolutionTrigger",
    "apply_session_capture",
    "attempt_stop_resolution",
    "build_integration_status",
    "decide_seam_action",
    "escalate_record_fact_outcome",
    "integration_action",
    "integration_owner_seam",
    "reduce",
    "resolve_conflicted_rebase",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "Event": ("ralph.pipeline.events", "Event"),
    "IntegrationAction": ("ralph.pipeline._integration_action", "IntegrationAction"),
    "IntegrationStatus": ("ralph.pipeline._integration_status", "IntegrationStatus"),
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
    "StopResolutionTrigger": (
        "ralph.pipeline.stop_resolution_trigger",
        "StopResolutionTrigger",
    ),
    "apply_session_capture": ("ralph.pipeline._runner_session", "apply_session_capture"),
    "attempt_stop_resolution": (
        "ralph.pipeline._stop_resolution_entry",
        "attempt_stop_resolution",
    ),
    "build_integration_status": (
        "ralph.pipeline._integration_status",
        "build_integration_status",
    ),
    "decide_seam_action": ("ralph.pipeline._integration_owner_seam", "decide_seam_action"),
    "escalate_record_fact_outcome": (
        "ralph.pipeline._integration_diagnostic_handoff",
        "escalate_record_fact_outcome",
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
