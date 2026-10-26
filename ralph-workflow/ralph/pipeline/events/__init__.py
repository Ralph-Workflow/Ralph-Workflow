"""Pipeline events representing all state transitions."""

from __future__ import annotations

from .agent_failure_event import (
    AgentFailureEvent,
    observation_from_event,
)
from .analysis_decision_event import AnalysisDecisionEvent
from .commit_residual_event import CommitResidualEvent
from .execution_result_event import ExecutionResultEvent
from .phase_failure_event import PhaseFailureEvent
from .pipeline_event import PipelineEvent
from .post_fanout_verification_event import PostFanoutVerificationEvent
from .worker_completed_event import WorkerCompletedEvent
from .worker_failed_event import WorkerFailedEvent
from .worker_started_event import WorkerStartedEvent

Event = (
    PipelineEvent
    | CommitResidualEvent
    | PhaseFailureEvent
    | ExecutionResultEvent
    | WorkerStartedEvent
    | WorkerCompletedEvent
    | WorkerFailedEvent
    | PostFanoutVerificationEvent
    | AnalysisDecisionEvent
    | AgentFailureEvent
)

__all__ = [
    "AgentFailureEvent",
    "AnalysisDecisionEvent",
    "CommitResidualEvent",
    "Event",
    "ExecutionResultEvent",
    "PhaseFailureEvent",
    "PipelineEvent",
    "PostFanoutVerificationEvent",
    "WorkerCompletedEvent",
    "WorkerFailedEvent",
    "WorkerStartedEvent",
    "observation_from_event",
]
