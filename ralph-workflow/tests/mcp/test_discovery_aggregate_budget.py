from __future__ import annotations

import io
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
from ralph.mcp.upstream.registry import UpstreamRegistry

if TYPE_CHECKING:
    from ralph.mcp.upstream.client import HttpUpstreamClient, StdioUpstreamClient


class _FakeMonotonic:
    def __init__(self) -> None:
        self._now: float = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _ProbeCounterClient:
    def __init__(self, server: UpstreamMcpServer, counter: list[int]) -> None:
        self._server = server
        self._counter = counter

    def list_tools(self, *, timeout_s: float | None = None) -> list[UpstreamTool]:
        self._counter.append(1)
        return [
            UpstreamTool(
                name=f"tool-{self._server.name}",
                description="counted fake",
                input_schema={"type": "object"},
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {}


class _TimeAdvancingClient:
    _probe_lock: threading.Lock = threading.Lock()

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
        with self._probe_lock:
            self._counter.append(1)
            if timeout_s is not None and self._probe_duration > timeout_s:
                self._time_advancer(timeout_s)
                from ralph.mcp.upstream.models import UpstreamCallError

                raise UpstreamCallError(
                    f"fake probe to {self._server.name} timed out after {timeout_s:.1f}s"
                )
            self._time_advancer(self._probe_duration)
            return [
                UpstreamTool(
                    name=f"tool-{self._server.name}",
                    description="time-advancing fake",
                    input_schema={"type": "object"},
                )
            ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {}


def _make_servers(*names: str) -> list[UpstreamMcpServer]:
    return [
        UpstreamMcpServer(
            name=name,
            transport="http",
            url=f"http://{name}.example/mcp",
        )
        for name in names
    ]


class TestDiscoveryBudgetConstant:
    def test_discovery_budget_is_strictly_below_preflight(self) -> None:
        preflight = mcp_preflight_timeout_from_env({})
        discovery = mcp_discovery_budget_from_env({})
        assert discovery < preflight

    def test_discovery_budget_leaves_child_failure_window(self) -> None:
        from ralph.mcp.protocol._startup_timeouts import _PROBE_SAFETY_S

        preflight = mcp_preflight_timeout_from_env({})
        discovery = mcp_discovery_budget_from_env({})
        remaining = preflight - discovery
        assert remaining.total_seconds() >= _PROBE_SAFETY_S

    def test_discovery_budget_at_defaults_28_seconds(self) -> None:
        discovery = mcp_discovery_budget_from_env({})
        assert discovery.total_seconds() == 28.0


class TestAggregateBudgetEnforcement:
    def test_four_slow_servers_probed_within_aggregate_budget(self) -> None:
        clock = _FakeMonotonic()
        servers = _make_servers("slow-1", "slow-2", "slow-3", "slow-4")
        counter: list[int] = []

        def slow_factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            return _TimeAdvancingClient(
                server, counter, probe_duration=9.5, time_advancer=clock.advance
            )

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                slow_factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )

        assert len(counter) == 3

        tools = registry.tool_definitions()
        assert len(tools) == 2

        assert clock() == 28.0
        discovery = mcp_discovery_budget_from_env({})
        assert clock() <= discovery.total_seconds()
        preflight = mcp_preflight_timeout_from_env({})
        assert clock() < preflight.total_seconds()

    def test_partial_servers_probed_before_aggregate_budget_exhausted(self) -> None:
        clock = _FakeMonotonic()
        servers = _make_servers("fast-1", "fast-2", "fast-3", "fast-4", "fast-5")
        counter: list[int] = []

        def advancing_factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            return _TimeAdvancingClient(
                server, counter, probe_duration=7.0, time_advancer=clock.advance
            )

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                advancing_factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )

        assert len(counter) == 4
        assert counter == [1, 1, 1, 1]

        tools = registry.tool_definitions()
        assert len(tools) == 4

    def test_single_fast_upstream_unaffected_by_aggregate_budget(self) -> None:
        clock = _FakeMonotonic()
        servers = _make_servers("fast")
        counter: list[int] = []

        def fast_factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            return _TimeAdvancingClient(
                server, counter, probe_duration=0.5, time_advancer=clock.advance
            )

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                fast_factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )

        tools = registry.tool_definitions()
        assert len(tools) == 1
        assert tools[0].alias == "ralph_upstream__fast__tool-fast"

        elapsed = clock()
        discovery = mcp_discovery_budget_from_env({})
        assert elapsed < discovery.total_seconds()

    def test_on_unreachable_raise_still_respected_when_budget_not_exhausted(self) -> None:
        clock = _FakeMonotonic()
        servers = _make_servers("fast-1", "fast-2", "fast-3")
        counter: list[int] = []

        def fast_factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            return _TimeAdvancingClient(
                server, counter, probe_duration=0.5, time_advancer=clock.advance
            )

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                fast_factory,
            ),
            on_unreachable="raise",
            _monotonic=clock,
        )

        assert len(counter) == 3
        tools = registry.tool_definitions()
        assert len(tools) == 3

    def test_zero_servers_returns_empty_registry(self) -> None:
        clock = _FakeMonotonic()
        registry = UpstreamRegistry.build(
            [],
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )
        assert registry.tool_definitions() == []
        assert clock() == 0.0

    def test_clock_not_advanced_when_server_skipped_due_to_budget(self) -> None:
        clock = _FakeMonotonic()
        servers = _make_servers("probe-1", "probe-2", "skipped-1", "skipped-2")
        counter: list[int] = []

        def factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            return _TimeAdvancingClient(
                server, counter, probe_duration=14.0, time_advancer=clock.advance
            )

        UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )

        assert len(counter) == 2
        assert clock() == 28.0

    def test_multiple_fast_probes_all_fit_within_budget(self) -> None:
        clock = _FakeMonotonic()
        servers = _make_servers(*(f"fast-{i}" for i in range(10)))
        counter: list[int] = []

        def multi_factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            return _TimeAdvancingClient(
                server, counter, probe_duration=2.0, time_advancer=clock.advance
            )

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                multi_factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )

        assert len(counter) == 10
        assert len(registry.tool_definitions()) == 10


class _TimeoutRecordingClient:
    def __init__(
        self,
        server: UpstreamMcpServer,
        time_advancer: Callable[[float], None],
        recorded_timeouts: list[float | None],
        probe_duration: float = 15.0,
    ) -> None:
        self._server = server
        self._time_advancer = time_advancer
        self._recorded_timeouts = recorded_timeouts
        self._probe_duration = probe_duration

    def list_tools(self, *, timeout_s: float | None = None) -> list[UpstreamTool]:
        self._recorded_timeouts.append(timeout_s)
        duration = (
            min(self._probe_duration, timeout_s) if timeout_s is not None else self._probe_duration
        )
        self._time_advancer(duration)
        if timeout_s is not None and self._probe_duration > timeout_s:
            from ralph.mcp.upstream.models import UpstreamCallError

            raise UpstreamCallError(f"probe to {self._server.name} timed out after {duration:.1f}s")
        return [
            UpstreamTool(
                name=f"tool-{self._server.name}",
                description="timeout-recording fake",
                input_schema={"type": "object"},
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {}


class TestHardBoundTimeoutEnforcement:
    def test_second_probe_receives_remaining_budget(self) -> None:
        clock = _FakeMonotonic()
        servers = _make_servers("slow-1", "slow-2")
        recorded_timeouts: list[float | None] = []

        def recording_factory(server: UpstreamMcpServer) -> _TimeoutRecordingClient:
            return _TimeoutRecordingClient(
                server, clock.advance, recorded_timeouts, probe_duration=15.0
            )

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                recording_factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )

        assert len(recorded_timeouts) == 2

        assert recorded_timeouts[0] == 28.0, (
            f"expected first call timeout_s=28.0, got {recorded_timeouts[0]}"
        )
        assert recorded_timeouts[1] == 13.0, (
            f"expected second call timeout_s=13.0 (28 - 15), got {recorded_timeouts[1]}"
        )

        assert clock() == 28.0
        assert clock() <= 28.0

        assert len(registry.tool_definitions()) == 1

    def test_second_probe_succeeds_when_within_remaining_budget(self) -> None:
        clock = _FakeMonotonic()
        servers = _make_servers("slow-1", "fast-2")
        recorded_timeouts: list[float | None] = []

        def recording_factory(server: UpstreamMcpServer) -> _TimeoutRecordingClient:
            duration = 15.0 if server.name == "slow-1" else 10.0
            return _TimeoutRecordingClient(
                server, clock.advance, recorded_timeouts, probe_duration=duration
            )

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                recording_factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )

        assert len(recorded_timeouts) == 2
        assert recorded_timeouts[0] == 28.0
        assert recorded_timeouts[1] == 13.0
        assert clock() == 25.0
        assert clock() <= 28.0
        assert len(registry.tool_definitions()) == 2

    def test_third_server_skipped_after_budget_exhausted(self) -> None:
        clock = _FakeMonotonic()
        servers = _make_servers("probe-1", "probe-2", "probe-3")
        recorded_timeouts: list[float | None] = []

        def recording_factory(server: UpstreamMcpServer) -> _TimeoutRecordingClient:
            return _TimeoutRecordingClient(
                server, clock.advance, recorded_timeouts, probe_duration=15.0
            )

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                recording_factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )

        assert len(recorded_timeouts) == 2
        assert recorded_timeouts[0] == 28.0
        assert recorded_timeouts[1] == 13.0

        assert clock() == 28.0
        assert clock() <= 28.0

        assert len(registry.tool_definitions()) == 1


class TestStrictModeFailsClosedOnBudgetExhaustion:
    def test_strict_mode_raises_when_budget_exhausted_before_first_server(
        self,
    ) -> None:
        from ralph.mcp.upstream.validation import UpstreamValidationError

        call_count = [0]

        def fake_clock() -> float:
            call_count[0] += 1
            if call_count[0] == 1:
                return 0.0
            return 29.0

        factory_calls: list[str] = []

        def make_client(
            server: UpstreamMcpServer,
        ) -> HttpUpstreamClient | StdioUpstreamClient:
            factory_calls.append(server.name)
            raise AssertionError("client must not be constructed when the deadline has expired")

        servers = [
            UpstreamMcpServer(
                name="strict-server",
                transport="http",
                url="http://strict.example/mcp",
                origin="custom",
            )
        ]

        with pytest.raises(UpstreamValidationError) as exc_info:
            UpstreamRegistry.build(
                servers,
                client_factory=make_client,
                on_unreachable="raise",
                _monotonic=fake_clock,
            )
        assert "aggregate discovery budget" in str(exc_info.value)
        assert "strict-server" in str(exc_info.value)
        assert factory_calls == []
        assert call_count[0] in (2, 3)

    def test_warn_and_skip_mode_still_skips_on_budget_exhaustion(self) -> None:
        call_count = [0]

        def fake_clock() -> float:
            call_count[0] += 1
            if call_count[0] == 1:
                return 0.0
            return 29.0

        factory_calls: list[str] = []

        def make_client(
            server: UpstreamMcpServer,
        ) -> HttpUpstreamClient | StdioUpstreamClient:
            factory_calls.append(server.name)
            raise AssertionError("client must not be constructed when the deadline has expired")

        servers = [
            UpstreamMcpServer(
                name="soft-server",
                transport="http",
                url="http://soft.example/mcp",
                origin="agent_upstream",
            )
        ]

        registry = UpstreamRegistry.build(
            servers,
            client_factory=make_client,
            on_unreachable="warn_and_skip",
            _monotonic=fake_clock,
        )

        assert registry.tool_definitions() == []
        assert factory_calls == []

    def test_strict_mode_partial_success_then_budget_exhausted_raises(
        self,
    ) -> None:
        from ralph.mcp.upstream.validation import UpstreamValidationError

        clock = _FakeMonotonic()
        counter: list[int] = []

        def slow_factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            return _TimeAdvancingClient(
                server, counter, probe_duration=14.0, time_advancer=clock.advance
            )

        servers = _make_servers("ok-1", "ok-2", "too-slow")

        with pytest.raises(UpstreamValidationError) as exc_info:
            UpstreamRegistry.build(
                servers,
                client_factory=cast(
                    "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                    slow_factory,
                ),
                on_unreachable="raise",
                _monotonic=clock,
            )
        assert "aggregate discovery budget" in str(exc_info.value)
        assert any(s.name in str(exc_info.value) for s in servers)
        assert len(counter) in (2, 3)

    def test_strict_mode_raises_when_probe_times_out_at_budget_limit(self) -> None:
        from ralph.mcp.upstream.validation import UpstreamValidationError

        clock = _FakeMonotonic()
        servers = _make_servers("strict-1", "strict-2")
        recorded_timeouts: list[float | None] = []

        def recording_factory(server: UpstreamMcpServer) -> _TimeoutRecordingClient:
            return _TimeoutRecordingClient(
                server, clock.advance, recorded_timeouts, probe_duration=15.0
            )

        with pytest.raises(UpstreamValidationError) as exc_info:
            UpstreamRegistry.build(
                servers,
                client_factory=cast(
                    "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                    recording_factory,
                ),
                on_unreachable="raise",
                _monotonic=clock,
            )

        assert len(recorded_timeouts) == 2
        assert recorded_timeouts[0] == 28.0
        assert recorded_timeouts[1] == 13.0
        assert "is unreachable" in str(exc_info.value)
        assert "strict-2" in str(exc_info.value)
        assert clock() == 28.0


class TestAggregateVsPerStageEnvelope:
    def test_aggregate_bound_is_preflight_enforced_by_deadline(self) -> None:
        from ralph.mcp.protocol import _startup_timeouts as mod

        preflight = mcp_preflight_timeout_from_env({}).total_seconds()
        discovery = mcp_discovery_budget_from_env({}).total_seconds()
        assert discovery < preflight
        per_stage_sum = mod._CONFIG_LOAD_BUDGET_S + mod._REGISTRY_BUILD_BUDGET_S + discovery
        assert per_stage_sum > discovery, "summing must not be the enforcement mechanism"
        from ralph.mcp.protocol._startup_timeouts import record_stage_elapsed

        result = record_stage_elapsed(
            "upstream_catalog_or_discovery_done",
            mod._REGISTRY_BUILD_BUDGET_S + 0.01,
            budget_s=mod._REGISTRY_BUILD_BUDGET_S,
        )
        assert result is None, (
            "per-stage overrun must be diagnostic (no raise, returns None); "
            "the aggregate deadline is the only fatal enforcement"
        )


class _RaisingClient:
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
        self._time_advancer(self._probe_duration)
        self._counter.append(1)
        from ralph.mcp.upstream.models import UpstreamCallError

        raise UpstreamCallError(f"fake connection refused to {self._server.name}")

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {}


class TestProbeOutcomeStructuredLogging:
    def test_failed_probe_records_elapsed_and_server_identity(self) -> None:
        clock = _FakeMonotonic()
        servers = _make_servers("failing-server")
        counter: list[int] = []

        def raising_factory(server: UpstreamMcpServer) -> _RaisingClient:
            return _RaisingClient(server, counter, probe_duration=5.0, time_advancer=clock.advance)

        sink = io.StringIO()
        handler_id = loguru_logger.add(sink, format="{message}", level="WARNING")
        try:
            registry = UpstreamRegistry.build(
                servers,
                client_factory=cast(
                    "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                    raising_factory,
                ),
                on_unreachable="warn_and_skip",
                _monotonic=clock,
            )
        finally:
            loguru_logger.remove(handler_id)

        assert registry.tool_definitions() == []

        captured = sink.getvalue()
        assert "ralph-mcp upstream probe:" in captured
        assert "server=failing-server" in captured
        assert "outcome=skipped" in captured
        assert "reason=UpstreamCallError" in captured
        assert "elapsed_ms=5000.0" in captured

    def test_strict_failed_probe_records_elapsed_and_server_identity(self) -> None:
        from ralph.mcp.upstream.validation import UpstreamValidationError

        clock = _FakeMonotonic()
        servers = _make_servers("strict-failing-server")
        counter: list[int] = []

        def raising_factory(server: UpstreamMcpServer) -> _RaisingClient:
            return _RaisingClient(server, counter, probe_duration=3.0, time_advancer=clock.advance)

        sink = io.StringIO()
        handler_id = loguru_logger.add(sink, format="{message}", level="ERROR")
        try:
            with pytest.raises(UpstreamValidationError):
                UpstreamRegistry.build(
                    servers,
                    client_factory=cast(
                        "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                        raising_factory,
                    ),
                    on_unreachable="raise",
                    _monotonic=clock,
                )
        finally:
            loguru_logger.remove(handler_id)

        captured = sink.getvalue()
        assert "ralph-mcp upstream probe:" in captured
        assert "server=strict-failing-server" in captured
        assert "outcome=strict_failure" in captured
        assert "reason=UpstreamCallError" in captured
        assert "elapsed_ms=3000.0" in captured

    def test_budget_exhausted_skip_records_server_identity(self) -> None:
        counter: list[int] = []

        def fake_clock() -> float:
            counter.append(1)
            if len(counter) == 1:
                return 0.0
            return 29.0

        servers = _make_servers("reached-server", "skipped-server")

        sink = io.StringIO()
        handler_id = loguru_logger.add(sink, format="{message}", level="WARNING")
        try:
            registry = UpstreamRegistry.build(
                servers,
                client_factory=cast(
                    "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                    lambda server: _TimeAdvancingClient(server, [], 0.0, lambda _: None),
                ),
                on_unreachable="warn_and_skip",
                _monotonic=fake_clock,
            )
        finally:
            loguru_logger.remove(handler_id)

        assert len(registry.tool_definitions()) == 0

        captured = sink.getvalue()
        assert "server=skipped-server" in captured
        assert "outcome=skipped" in captured
        assert "reason=budget_exhausted" in captured
        assert "elapsed_ms=0.0" in captured
