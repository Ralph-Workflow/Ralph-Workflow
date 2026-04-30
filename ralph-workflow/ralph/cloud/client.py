"""Cloud API client for Ralph workflow telemetry and metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field


class CloudConfigurationError(ValueError):
    """Raised when cloud reporting configuration is invalid."""


@dataclass(frozen=True)
class CloudClientConfig:
    """Runtime configuration for the cloud API client."""

    enabled: bool = False
    api_url: str | None = None
    api_key: str | None = None
    timeout_secs: float = 30.0
    graceful_degradation: bool = True


class ProgressEventType(StrEnum):
    """Structured workflow progress event type."""

    PIPELINE_STARTED = "pipeline_started"
    PHASE_TRANSITION = "phase_transition"
    ITERATION_STARTED = "iteration_started"
    ITERATION_PROGRESS = "iteration_progress"
    ITERATION_COMPLETED = "iteration_completed"
    REVIEW_PASS_STARTED = "review_pass_started"
    REVIEW_PROGRESS = "review_progress"
    REVIEW_PASS_COMPLETED = "review_pass_completed"
    AGENT_INVOKED = "agent_invoked"
    AGENT_COMPLETED = "agent_completed"
    CHECKPOINT_SAVED = "checkpoint_saved"
    COMMIT_CREATED = "commit_created"
    PUSH_COMPLETED = "push_completed"
    PUSH_FAILED = "push_failed"
    PULL_REQUEST_CREATED = "pull_request_created"
    PULL_REQUEST_FAILED = "pull_request_failed"
    PIPELINE_COMPLETED = "pipeline_completed"
    PIPELINE_INTERRUPTED = "pipeline_interrupted"
    HEARTBEAT = "heartbeat"
    CHILD_WAITING_SUSPECTED_FROZEN = "child_waiting_suspected_frozen"
    CHILD_WAITING_HARD_STOP = "child_waiting_hard_stop"


class _FrozenCloudModel(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Private base for frozen cloud payload models.

    Owns `model_config = ConfigDict(frozen=True)` once so descendants do not
    repeat it. Pydantic v2 inherits `model_config` when descendants do not
    declare one of their own.
    """

    model_config = ConfigDict(frozen=True)


class ProgressUpdate(_FrozenCloudModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Progress update payload sent to the cloud API."""

    timestamp: datetime
    phase: str
    message: str
    event_type: ProgressEventType
    previous_phase: str | None = None
    iteration: int | None = None
    total_iterations: int | None = None
    review_pass: int | None = None
    total_review_passes: int | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class PipelineResult(_FrozenCloudModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Final workflow completion payload."""

    success: bool
    iterations_used: int
    review_passes_used: int
    issues_found: bool
    duration_secs: int
    commit_sha: str | None = None
    pr_url: str | None = None
    push_count: int = 0
    last_pushed_commit: str | None = None
    unpushed_commits: list[str] = Field(default_factory=list)
    last_push_error: str | None = None
    error_message: str | None = None


class TelemetryEvent(_FrozenCloudModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Structured telemetry event payload."""

    timestamp: datetime
    name: str
    attributes: dict[str, object] = Field(default_factory=dict)


class MetricSample(_FrozenCloudModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Single numeric metric sample."""

    name: str
    value: float
    tags: dict[str, str] = Field(default_factory=dict)


class MetricsReport(_FrozenCloudModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Batch of numeric metrics for a workflow run."""

    samples: list[MetricSample] = Field(default_factory=list)


class HeartbeatPayload(_FrozenCloudModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Heartbeat payload sent to the API."""

    timestamp: datetime


class CloudClient:
    """HTTP client for Ralph cloud progress, telemetry, and metrics reporting."""

    def __init__(
        self,
        config: CloudClientConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._client = httpx.Client(
            timeout=config.timeout_secs,
            transport=transport,
            headers=self._build_headers(config.api_key),
        )

    @staticmethod
    def _build_headers(api_key: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def build_url(api_url: str, path: str) -> str:
        base = api_url.strip()
        if not base.lower().startswith("https://"):
            msg = "Cloud API URL must use https://"
            raise CloudConfigurationError(msg)

        return f"{base.rstrip('/')}/{path.lstrip('/')}"

    def _require_url(self) -> str:
        if not self.config.api_url:
            msg = "Cloud API URL is required when cloud reporting is enabled"
            raise CloudConfigurationError(msg)
        return self.config.api_url

    def _require_api_key(self) -> None:
        if not self.config.api_key:
            msg = "Cloud API key is required when cloud reporting is enabled"
            raise CloudConfigurationError(msg)

    def _post_model(self, run_id: str, path: str, payload: BaseModel) -> bool:
        if not self.config.enabled:
            return False

        self._require_api_key()
        url = self.build_url(self._require_url(), f"runs/{run_id}/{path}")

        try:
            response = self._client.post(url, content=payload.model_dump_json())
            response.raise_for_status()
        except (httpx.HTTPError, CloudConfigurationError):
            if not self.config.graceful_degradation:
                raise
            logger.warning("Cloud reporting failed for {}", path)
            return False
        return True

    def report_progress(self, run_id: str, update: ProgressUpdate) -> bool:
        """Report a structured progress update for a run."""

        return self._post_model(run_id, "progress", update)

    def send_heartbeat(self, run_id: str, *, at: datetime | None = None) -> bool:
        """Send a liveness heartbeat for a run."""

        heartbeat = HeartbeatPayload(timestamp=at or datetime.now(UTC))
        return self._post_model(run_id, "heartbeat", heartbeat)

    def report_completion(self, run_id: str, result: PipelineResult) -> bool:
        """Report final workflow completion details."""

        return self._post_model(run_id, "complete", result)

    def report_telemetry(self, run_id: str, event: TelemetryEvent) -> bool:
        """Send a telemetry event for richer workflow tracing."""

        return self._post_model(run_id, "telemetry", event)

    def report_metrics(self, run_id: str, report: MetricsReport) -> bool:
        """Send batched numeric metrics for a workflow run."""

        return self._post_model(run_id, "metrics", report)

    def close(self) -> None:
        """Close the underlying HTTP client."""

        self._client.close()

    def __enter__(self) -> CloudClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
