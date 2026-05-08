"""Backward-compatible cloud reporting exports.

Provides a stable ``ralph.api.cloud`` import surface for callers that used the
original import path before cloud reporting was moved to ``ralph.cloud.client``.
All names here are re-exported from that module; no additional logic lives here.

Key names exported:

- ``CloudReporter`` (alias for ``CloudClient``) - submits pipeline telemetry
- ``CloudClientConfig`` - connection configuration (endpoint, token, etc.)
- ``PipelineReport`` (alias for ``ProgressUpdate``) - progress event payload
- ``PipelineResult``, ``TelemetryEvent``, ``MetricsReport``, ``MetricSample``,
  ``ProgressEventType`` - supporting types for the telemetry protocol
- ``CloudConfigurationError`` - raised when the cloud client cannot initialise

New code should import directly from ``ralph.cloud.client``.
"""

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
