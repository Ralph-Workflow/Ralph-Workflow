from __future__ import annotations

import concurrent.futures
import io
import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import pytest
from loguru import logger as loguru_logger

from ralph.mcp.protocol._startup_timeouts import (
    mcp_discovery_budget_from_env,
    mcp_preflight_timeout_from_env,
)
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamTool
from ralph.mcp.upstream.registry import (
    _MAX_PROBE_WORKERS,
    UpstreamRegistry,
)

if TYPE_CHECKING:
    from ralph.mcp.upstream.client import HttpUpstreamClient, StdioUpstreamClient


class _FakeMonotonic:
    def __init__(self) -> None:
        self._now: float = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _make_servers(*names: str) -> list[UpstreamMcpServer]:
    return [
        UpstreamMcpServer(
            name=name,
            transport="http",
            url=f"http://{name}.example/mcp",
        )
        for name in names
    ]


class _BarrierProbedClient:
    def __init__(
        self,
        server: UpstreamMcpServer,
        barrier: threading.Barrier,
        counter: list[int],
    ) -> None:
        self._server = server
        self._barrier = barrier
        self._counter = counter

    def list_tools(self, *, timeout_s: float | None = None) -> list[UpstreamTool]:
        self._counter.append(1)
        self._barrier.wait(timeout=2.0)
        return [
            UpstreamTool(
                name=f"tool-{self._server.name}",
                description="barrier-probed fake",
                input_schema={"type": "object"},
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {}


class _SequentialAdvancingClient:
    def __init__(
        self,
        server: UpstreamMcpServer,
        counter: list[int],
        probe_duration: float,
        time_advancer: Callable[[float], None],
    ) -> None:
        self._server = server
        self._counter = counter
        self._probe_duration = probe_duration
        self._time_advancer = time_advancer

    def list_tools(self, *, timeout_s: float | None = None) -> list[UpstreamTool]:
        self._counter.append(1)
        if timeout_s is not None and self._probe_duration > timeout_s:
            from ralph.mcp.upstream.models import UpstreamCallError

            self._time_advancer(timeout_s)
            raise UpstreamCallError(
                f"fake probe to {self._server.name} timed out after {timeout_s:.1f}s"
            )
        self._time_advancer(self._probe_duration)
        return [
            UpstreamTool(
                name=f"tool-{self._server.name}",
                description="sequential-advancing fake",
                input_schema={"type": "object"},
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {}


class _LateSuccessfulClient:
    def __init__(
        self,
        server: UpstreamMcpServer,
        time_advancer: Callable[[float], None],
        advance_by: float,
    ) -> None:
        self._server = server
        self._time_advancer = time_advancer
        self._advance_by = advance_by

    def list_tools(self, *, timeout_s: float | None = None) -> list[UpstreamTool]:
        self._time_advancer(self._advance_by)
        return [
            UpstreamTool(
                name=f"tool-{self._server.name}",
                description="late successful fake",
                input_schema={"type": "object"},
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {}


def test_max_probe_workers_in_sane_range() -> None:
    assert 1 <= _MAX_PROBE_WORKERS <= 32


def test_empty_input_returns_empty_registry_without_constructing_executor() -> None:
    import concurrent.futures

    executor_constructed: list[concurrent.futures.ThreadPoolExecutor] = []

    real_executor = concurrent.futures.ThreadPoolExecutor

    def spy_executor(*args: object, **kwargs: object) -> concurrent.futures.ThreadPoolExecutor:
        instance = real_executor(*args, **kwargs)
        executor_constructed.append(instance)
        return instance

    clock = _FakeMonotonic()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "ralph.mcp.upstream.registry.concurrent.futures.ThreadPoolExecutor", spy_executor
        )
        registry = UpstreamRegistry.build(
            [],
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )
    assert registry.tool_definitions() == []
    assert clock() == 0.0
    assert executor_constructed == []


def test_concurrent_fanout_uses_barrier_for_wall_clock_proof() -> None:
    clock = _FakeMonotonic()
    servers = _make_servers("a", "b", "c", "d")
    counter: list[int] = []
    barrier = threading.Barrier(parties=len(servers) + 1)

    def factory(server: UpstreamMcpServer) -> _BarrierProbedClient:
        return _BarrierProbedClient(server, barrier, counter)

    registry_holder: list[UpstreamRegistry] = []
    build_exception: list[BaseException] = []

    def run_build() -> None:
        try:
            registry_holder.append(
                UpstreamRegistry.build(
                    servers,
                    client_factory=cast(
                        "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                        factory,
                    ),
                    on_unreachable="warn_and_skip",
                    _monotonic=clock,
                )
            )
        except BaseException as exc:
            build_exception.append(exc)

    worker_thread = threading.Thread(target=run_build, daemon=True)
    worker_thread.start()
    barrier.wait(timeout=2.0)
    clock.advance(4.0)
    worker_thread.join(timeout=2.0)

    assert build_exception == [], f"build raised: {build_exception!r}"
    assert len(registry_holder) == 1
    assert len(counter) == 4
    assert len(registry_holder[0].tool_definitions()) == 4
    assert clock() == 4.0


def test_sequential_analogue_rejects_after_deadline() -> None:
    clock = _FakeMonotonic()
    servers = _make_servers("a", "b", "c", "d")
    counter: list[int] = []

    def factory(server: UpstreamMcpServer) -> _SequentialAdvancingClient:
        return _SequentialAdvancingClient(
            server, counter, probe_duration=4.0, time_advancer=clock.advance
        )

    import os

    os.environ["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"] = "12000"
    try:
        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )
    finally:
        del os.environ["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"]

    assert len(counter) <= 4
    tools = registry.tool_definitions()
    assert len(tools) <= 2
    assert clock() >= 10.0


def test_all_late_results_discarded_under_strict_mode() -> None:
    import os

    from ralph.mcp.upstream.validation import UpstreamValidationError

    os.environ["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"] = "8000"
    try:
        clock = _FakeMonotonic()
        servers = _make_servers("a", "b", "c", "d")
        counter: list[int] = []

        def factory(server: UpstreamMcpServer) -> _SequentialAdvancingClient:
            return _SequentialAdvancingClient(
                server, counter, probe_duration=4.0, time_advancer=clock.advance
            )

        with pytest.raises(UpstreamValidationError) as exc_info:
            UpstreamRegistry.build(
                servers,
                client_factory=cast(
                    "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                    factory,
                ),
                on_unreachable="raise",
                _monotonic=clock,
            )
        msg = str(exc_info.value)
        assert "unreachable" in msg
        assert 1 <= len(counter) <= 4

        clock2 = _FakeMonotonic()
        counter2: list[int] = []

        def factory2(server: UpstreamMcpServer) -> _SequentialAdvancingClient:
            return _SequentialAdvancingClient(
                server, counter2, probe_duration=4.0, time_advancer=clock2.advance
            )

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                factory2,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock2,
        )
        assert 0 <= len(registry.tool_definitions()) <= 2
        assert 1 <= len(counter2) <= 4
    finally:
        del os.environ["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"]


def test_strict_mode_collector_expiry_raises_with_short_preflight() -> None:
    from ralph.mcp.upstream.validation import UpstreamValidationError

    os.environ["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"] = "6000"
    try:
        clock = _FakeMonotonic()
        servers = _make_servers("a", "b", "c")
        counter: list[int] = []

        def factory(server: UpstreamMcpServer) -> _SequentialAdvancingClient:
            return _SequentialAdvancingClient(
                server, counter, probe_duration=20.0, time_advancer=clock.advance
            )

        with pytest.raises(UpstreamValidationError) as exc_info:
            UpstreamRegistry.build(
                servers,
                client_factory=cast(
                    "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                    factory,
                ),
                on_unreachable="raise",
                _monotonic=clock,
            )
        assert "unreachable" in str(exc_info.value)
        assert 1 <= len(counter) <= 3

        clock2 = _FakeMonotonic()
        counter2: list[int] = []

        def factory2(server: UpstreamMcpServer) -> _SequentialAdvancingClient:
            return _SequentialAdvancingClient(
                server, counter2, probe_duration=20.0, time_advancer=clock2.advance
            )

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                factory2,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock2,
        )
        assert registry.tool_definitions() == []
    finally:
        del os.environ["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"]


def test_discovery_budget_strictly_below_preflight_unchanged() -> None:
    preflight = mcp_preflight_timeout_from_env({})
    discovery = mcp_discovery_budget_from_env({})
    assert discovery < preflight


def test_as_completed_timeout_forces_collector_expiry() -> None:
    from ralph.mcp.upstream.validation import UpstreamValidationError

    servers = _make_servers("srv-1", "srv-2")

    def timeout_as_completed(*args: object, **kwargs: object) -> object:
        class _RaisingIterator:
            def __iter__(self) -> _RaisingIterator:
                return self

            def __next__(self) -> object:
                raise concurrent.futures.TimeoutError("forced collector timeout")

        return _RaisingIterator()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "ralph.mcp.upstream.registry.concurrent.futures.as_completed",
            timeout_as_completed,
        )

        with pytest.raises(UpstreamValidationError) as exc_info:
            UpstreamRegistry.build(
                servers,
                client_factory=cast(
                    "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                    lambda s: _LateSuccessfulClient(s, lambda _: None, 0.0),
                ),
                on_unreachable="raise",
            )
        assert "is unreachable" in str(exc_info.value)
        assert any(name in str(exc_info.value) for name in ("srv-1", "srv-2"))

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                lambda s: _LateSuccessfulClient(s, lambda _: None, 0.0),
            ),
            on_unreachable="warn_and_skip",
        )
        assert registry.tool_definitions() == []


def test_all_late_successful_completion_discarded() -> None:
    from ralph.mcp.upstream.validation import UpstreamValidationError

    servers = _make_servers("late-1", "late-2")

    clock_strict = _FakeMonotonic()
    with pytest.raises(UpstreamValidationError) as exc_info:
        UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                lambda s: _LateSuccessfulClient(s, clock_strict.advance, 30.0),
            ),
            on_unreachable="raise",
            _monotonic=clock_strict,
        )
    assert "is unreachable" in str(exc_info.value)
    assert any(name in str(exc_info.value) for name in ("late-1", "late-2"))

    clock_warn = _FakeMonotonic()
    registry = UpstreamRegistry.build(
        servers,
        client_factory=cast(
            "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
            lambda s: _LateSuccessfulClient(s, clock_warn.advance, 30.0),
        ),
        on_unreachable="warn_and_skip",
        _monotonic=clock_warn,
    )
    assert registry.tool_definitions() == []


def test_late_successful_probe_logs_diagnostic() -> None:
    clock = _FakeMonotonic()
    servers = _make_servers("late-1")

    sink = io.StringIO()
    handler_id = loguru_logger.add(sink, format="{message}", level="WARNING")
    try:
        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                lambda s: _LateSuccessfulClient(s, clock.advance, 30.0),
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )
    finally:
        loguru_logger.remove(handler_id)

    assert registry.tool_definitions() == []

    log_text = sink.getvalue()
    assert "ralph-mcp upstream probe: server=late-1" in log_text, (
        f"missing late-1 diagnostic; captured: {log_text!r}"
    )
    assert "outcome=skipped" in log_text, f"missing outcome=skipped; captured: {log_text!r}"
    assert "reason=budget_exhausted" in log_text, (
        f"missing reason=budget_exhausted; captured: {log_text!r}"
    )
    assert "elapsed_ms=30000.0" in log_text, (
        f"missing worker-computed elapsed_ms=30000.0; captured: {log_text!r}"
    )
