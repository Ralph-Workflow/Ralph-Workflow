"""Public pipeline state and reducer exports.

This package is the core of Ralph's orchestration loop. It exposes the public
state/event types most callers need, plus the pure ``reduce`` function used to
advance the state machine.
"""

from ralph.config.enums import PipelinePhase
from ralph.pipeline.events import Event, PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState

__all__ = [
    "Event",
    "PipelineEvent",
    "PipelinePhase",
    "PipelineState",
    "reduce",
]
