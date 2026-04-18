"""Unit tests for the cloud API client."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from ralph.cloud.client import (
    CloudClient,
    CloudClientConfig,
    CloudConfigurationError,
    MetricSample,
    MetricsReport,
    PipelineResult,
    ProgressEventType,
    ProgressUpdate,
    TelemetryEvent,
)


def _transport(handler: httpx.MockTransport) -> CloudClient:
    return CloudClient(
        CloudClientConfig(
            enabled=True,
            api_url="https://api.example.com/v1/",
            api_key="secret-token",
            timeout_secs=5,
        ),
        transport=handler,
    )


def test_build_url_joins_base_and_path_without_double_slashes() -> None:
    assert (
        CloudClient.build_url("https://api.example.com/v1/", "/runs/run-1/progress")
        == "https://api.example.com/v1/runs/run-1/progress"
    )


@pytest.mark.parametrize("invalid_url", ["http://api.example.com", "ftp://api.example.com"])
def test_build_url_rejects_non_https_urls(invalid_url: str) -> None:
    with pytest.raises(CloudConfigurationError, match="https://"):
        CloudClient.build_url(invalid_url, "/runs/run-1/progress")


def test_report_progress_posts_expected_payload_and_headers() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["Authorization"]
        captured["content_type"] = request.headers["Content-Type"]
        captured["json"] = request.read().decode("utf-8")
        return httpx.Response(status_code=202, json={"accepted": True})

    client = _transport(httpx.MockTransport(handler))
    update = ProgressUpdate(
        timestamp=datetime(2026, 4, 13, 12, 0, tzinfo=UTC),
        phase="development",
        message="Iteration started",
        event_type=ProgressEventType.ITERATION_STARTED,
        iteration=1,
        total_iterations=5,
    )

    assert client.report_progress("run-1", update) is True
    assert captured == {
        "url": "https://api.example.com/v1/runs/run-1/progress",
        "auth": "Bearer secret-token",
        "content_type": "application/json",
        "json": (
            '{"timestamp":"2026-04-13T12:00:00Z","phase":"development",'
            '"message":"Iteration started","event_type":"iteration_started",'
            '"previous_phase":null,"iteration":1,"total_iterations":5,'
            '"review_pass":null,"total_review_passes":null,"metadata":{}}'
        ),
    }


def test_send_heartbeat_posts_timestamp_payload() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(status_code=200, json={"ok": True})

    client = _transport(httpx.MockTransport(handler))

    at = datetime(2026, 4, 13, 12, 30, tzinfo=UTC)
    assert client.send_heartbeat("run-2", at=at) is True
    assert seen == {
        "url": "https://api.example.com/v1/runs/run-2/heartbeat",
        "json": '{"timestamp":"2026-04-13T12:30:00Z"}',
    }


def test_report_completion_posts_pipeline_result() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(status_code=200, json={"ok": True})

    client = _transport(httpx.MockTransport(handler))
    result = PipelineResult(
        success=True,
        iterations_used=3,
        review_passes_used=1,
        issues_found=False,
        duration_secs=42,
        commit_sha="abc123",
        push_count=1,
        last_pushed_commit="abc123",
    )

    assert client.report_completion("run-3", result) is True
    assert seen == {
        "url": "https://api.example.com/v1/runs/run-3/complete",
        "json": (
            '{"success":true,"iterations_used":3,"review_passes_used":1,'
            '"issues_found":false,"duration_secs":42,"commit_sha":"abc123",'
            '"pr_url":null,"push_count":1,"last_pushed_commit":"abc123",'
            '"unpushed_commits":[],"last_push_error":null,"error_message":null}'
        ),
    }


def test_report_telemetry_posts_structured_event() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(status_code=200, json={"ok": True})

    client = _transport(httpx.MockTransport(handler))
    event = TelemetryEvent(
        timestamp=datetime(2026, 4, 13, 12, 45, tzinfo=UTC),
        name="agent_completed",
        attributes={"agent": "claude", "duration_ms": 1250},
    )

    assert client.report_telemetry("run-4", event) is True
    assert seen == {
        "url": "https://api.example.com/v1/runs/run-4/telemetry",
        "json": (
            '{"timestamp":"2026-04-13T12:45:00Z","name":"agent_completed",'
            '"attributes":{"agent":"claude","duration_ms":1250}}'
        ),
    }


def test_report_metrics_posts_metric_batch() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(status_code=200, json={"ok": True})

    client = _transport(httpx.MockTransport(handler))
    report = MetricsReport(
        samples=[
            MetricSample(name="pipeline.duration_secs", value=42, tags={"phase": "review"}),
            MetricSample(name="pipeline.iterations", value=3, tags={"status": "success"}),
        ]
    )

    assert client.report_metrics("run-5", report) is True
    assert seen == {
        "url": "https://api.example.com/v1/runs/run-5/metrics",
        "json": (
            '{"samples":[{"name":"pipeline.duration_secs","value":42.0,'
            '"tags":{"phase":"review"}},{"name":"pipeline.iterations",'
            '"value":3.0,"tags":{"status":"success"}}]}'
        ),
    }


def test_disabled_client_skips_requests() -> None:
    client = CloudClient(
        CloudClientConfig(enabled=False, api_url="https://api.example.com", api_key="secret")
    )

    update = ProgressUpdate(
        timestamp=datetime(2026, 4, 13, 12, 0, tzinfo=UTC),
        phase="planning",
        message="Pipeline started",
        event_type=ProgressEventType.PIPELINE_STARTED,
    )

    assert client.report_progress("run-disabled", update) is False


def test_http_failures_are_swallowed_when_graceful_degradation_enabled() -> None:
    client = CloudClient(
        CloudClientConfig(
            enabled=True,
            api_url="https://api.example.com",
            api_key="secret",
            graceful_degradation=True,
        ),
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code=503, text="down")),
    )

    update = ProgressUpdate(
        timestamp=datetime(2026, 4, 13, 12, 0, tzinfo=UTC),
        phase="planning",
        message="Pipeline started",
        event_type=ProgressEventType.PIPELINE_STARTED,
    )

    assert client.report_progress("run-soft-fail", update) is False


def test_http_failures_raise_when_graceful_degradation_disabled() -> None:
    client = CloudClient(
        CloudClientConfig(
            enabled=True,
            api_url="https://api.example.com",
            api_key="secret",
            graceful_degradation=False,
        ),
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code=500, text="boom")),
    )

    update = ProgressUpdate(
        timestamp=datetime(2026, 4, 13, 12, 0, tzinfo=UTC),
        phase="planning",
        message="Pipeline started",
        event_type=ProgressEventType.PIPELINE_STARTED,
    )

    with pytest.raises(httpx.HTTPStatusError):
        client.report_progress("run-hard-fail", update)
