"""Backward-compatible cloud reporting exports."""

from ralph.cloud.client import (
    CloudClient as CloudReporter,
)
from ralph.cloud.client import (
    CloudClientConfig,
    CloudConfigurationError,
    MetricSample,
    MetricsReport,
    PipelineResult,
    ProgressEventType,
    TelemetryEvent,
)
from ralph.cloud.client import (
    ProgressUpdate as PipelineReport,
)

__all__ = [
    "CloudClientConfig",
    "CloudConfigurationError",
    "CloudReporter",
    "MetricSample",
    "MetricsReport",
    "PipelineReport",
    "PipelineResult",
    "ProgressEventType",
    "TelemetryEvent",
]
