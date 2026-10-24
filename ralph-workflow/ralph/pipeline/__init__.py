"""Public pipeline state and reducer exports.

This package is the core of Ralph's orchestration loop. It exposes the public
state/event types most callers need, plus the pure ``reduce`` function used to
advance the state machine.
"""

from ralph.config.enums import PipelinePhase
from ralph.pipeline import _integration_action as integration_action
from ralph.pipeline import _integration_owner_seam as integration_owner_seam
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_owner_seam import decide_seam_action
from ralph.pipeline._runner_session import apply_session_capture
from ralph.pipeline.events import Event, PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState

__all__ = [
    "Event",
    "IntegrationAction",
    "PipelineEvent",
    "PipelinePhase",
    "PipelineState",
    "apply_session_capture",
    "decide_seam_action",
    "integration_action",
    "integration_owner_seam",
    "reduce",
]
