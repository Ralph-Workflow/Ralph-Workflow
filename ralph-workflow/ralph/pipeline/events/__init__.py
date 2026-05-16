"""Pipeline events representing all state transitions."""

from __future__ import annotations

from .analysis_decision_event import AnalysisDecisionEvent
from .phase_failure_event import PhaseFailureEvent
from .pipeline_event import PipelineEvent
from .post_fanout_verification_event import PostFanoutVerificationEvent
from .worker_completed_event import WorkerCompletedEvent
from .worker_failed_event import WorkerFailedEvent
from .worker_started_event import WorkerStartedEvent

Event = (
    PipelineEvent
    | PhaseFailureEvent
    | WorkerStartedEvent
    | WorkerCompletedEvent
    | WorkerFailedEvent
    | PostFanoutVerificationEvent
    | AnalysisDecisionEvent
)

__all__ = [
    "AnalysisDecisionEvent",
    "Event",
    "PhaseFailureEvent",
    "PipelineEvent",
    "PostFanoutVerificationEvent",
    "WorkerCompletedEvent",
    "WorkerFailedEvent",
    "WorkerStartedEvent",
]
