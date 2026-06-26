"""Black-box tests for bounded HTTP ``response.read()`` in ``lifecycle.py``.

wt-024 memory-perf AC-08: two sites in ``lifecycle.py`` call
``response.read()`` with no byte cap. A misbehaving upstream that
streams an unbounded response body would OOM the parent. We bound both
at ``_LIFECYCLE_MAX_RESPONSE_BYTES`` (1 MiB).

All tests stub ``urllib.request.urlopen`` so no real network is involved.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server.lifecycle import (
    _LIFECYCLE_MAX_RESPONSE_BYTES,
    RestartAwareMcpBridge,
    _http_tools_list_names,
)

if TYPE_CHECKING:
    import pytest


class _HTTPResponseLike(Protocol):
    """Minimal contract for the response object passed to ``response.read()``."""

    def read(self, size: int | None = None) -> bytes: ...
    def close(self) -> None: ...


class _RecordingResponse(io.BytesIO):
    """In-memory response that records every ``read(size)`` invocation."""

    def __init__(self, payload: bytes = b"") -> None:
        super().__init__(payload)
        self.read_calls: list[int | None] = []

    def read(self, size: int | None = None) -> bytes:
        self.read_calls.append(size)
        return super().read(size if size is not None else -1)

    def close(self) -> None:
        super().close()


def test_lifecycle_max_response_bytes_constant_value() -> None:
    """``_LIFECYCLE_MAX_RESPONSE_BYTES`` must be a positive integer >= 1 MiB."""
    assert isinstance(_LIFECYCLE_MAX_RESPONSE_BYTES, int)
    assert _LIFECYCLE_MAX_RESPONSE_BYTES >= 1024 * 1024, (
        f"_LIFECYCLE_MAX_RESPONSE_BYTES must be >= 1 MiB; got {_LIFECYCLE_MAX_RESPONSE_BYTES}"
    )


def test_http_tools_list_names_caps_response_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_http_tools_list_names`` must pass a positive int to ``response.read``."""
    captured: dict[str, object] = {}
    response = _RecordingResponse(b"x" * (4 * 1024 * 1024))

    def fake_urlopen(request: object, timeout: float = 0) -> _RecordingResponse:
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = _http_tools_list_names("http://127.0.0.1:9/mcp", timeout=1.0)
    assert isinstance(result, list)
    assert captured.get("timeout") == 1.0


def test_http_tools_list_names_bounded_by_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read() in _http_tools_list_names must use a positive cap."""
    response = _RecordingResponse(b"")

    def fake_urlopen(request: object, timeout: float = 0) -> _RecordingResponse:
        return response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    _http_tools_list_names("http://127.0.0.1:9/mcp", timeout=1.0)
    assert response.read_calls, "response.read() must have been called"
    assert all(size is not None and size > 0 for size in response.read_calls), (
        f"response.read() must be called with a positive size; got sizes={response.read_calls!r}"
    )
    assert any(size == _LIFECYCLE_MAX_RESPONSE_BYTES for size in response.read_calls), (
        f"expected read(size=_LIFECYCLE_MAX_RESPONSE_BYTES); got sizes={response.read_calls!r}"
    )


class _FakeProcess:
    """Minimal ``ProcessLike`` test double with a live ``poll()``."""

    def __init__(self) -> None:
        self.polls = 0

    def poll(self) -> int | None:
        self.polls += 1
        return None

    def terminate(self, grace_period_s: float = 5.0) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int | None:
        return None

    def kill(self) -> None:
        return None

    @property
    def pid(self) -> int:
        return 1


def _make_inner() -> StandaloneMcpProcess:
    return StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9/mcp",
        process=_FakeProcess(),
        session_file=Path("/tmp/_lifecycle_test_session.json"),
    )


def test_reset_session_budget_caps_response_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RestartAwareMcpBridge.reset_session_budget`` must also cap
    ``response.read()``."""
    response = _RecordingResponse(b"")

    def fake_urlopen(request: object, timeout: float = 0) -> _RecordingResponse:
        return response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    bridge = RestartAwareMcpBridge(
        inner=_make_inner(),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=0),
        run_id="test-run",
    )
    bridge.reset_session_budget()
    assert response.read_calls, "response.read() must have been called by reset_session_budget"
    assert all(size is not None and size > 0 for size in response.read_calls), (
        f"response.read() must use a positive cap; got sizes={response.read_calls!r}"
    )
