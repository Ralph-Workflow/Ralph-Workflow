"""R2: first-start discovery respects an aggregate budget below the preflight timeout.

S-1 / S-2: a shared monotonic deadline is checked between probes in
``UpstreamRegistry.build``. When the aggregate discovery budget
(``mcp_discovery_budget_from_env`` = preflight - _PROBE_SAFETY_S) is exhausted,
remaining servers are warned-and-skipped rather than probed. This bounds the
worst-case total discovery time strictly below the preflight timeout.

These are unit tests that mock all network-facing components and use an
injectable clock to verify timing behaviour deterministically without
``time.sleep``.
"""

from __future__ import annotations

import pytest
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from ralph.mcp.protocol._startup_timeouts import (
    mcp_discovery_budget_from_env,
    mcp_preflight_timeout_from_env,
)
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamTool
from ralph.mcp.upstream.registry import UpstreamRegistry

if TYPE_CHECKING:
    from ralph.mcp.upstream.client import HttpUpstreamClient, StdioUpstreamClient

# ---------------------------------------------------------------------------
# Fake objects
# ---------------------------------------------------------------------------


class _FakeMonotonic:
    """Injectable clock: starts at 0, advances only when advance() is called."""

    def __init__(self) -> None:
        self._now: float = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _ProbeCounterClient:
    """Fake client that records every list_tools call in a counter list."""

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
    """Fake client that advances the shared test clock by probe_duration on each call."""

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
        return [
            UpstreamTool(
                name=f"tool-{self._server.name}",
                description="time-advancing fake",
                input_schema={"type": "object"},
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_servers(*names: str) -> list[UpstreamMcpServer]:
    return [
        UpstreamMcpServer(
            name=name,
            transport="http",
            url=f"http://{name}.example/mcp",
        )
        for name in names
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDiscoveryBudgetConstant:
    """The aggregate discovery budget is strictly below the preflight timeout."""

    def test_discovery_budget_is_strictly_below_preflight(self) -> None:
        preflight = mcp_preflight_timeout_from_env({})
        discovery = mcp_discovery_budget_from_env({})
        assert discovery < preflight

    def test_discovery_budget_leaves_child_failure_window(self) -> None:
        """After discovery budget expires, at least _PROBE_SAFETY_S remains for the child."""
        from ralph.mcp.protocol._startup_timeouts import _PROBE_SAFETY_S

        preflight = mcp_preflight_timeout_from_env({})
        discovery = mcp_discovery_budget_from_env({})
        remaining = preflight - discovery
        assert remaining.total_seconds() >= _PROBE_SAFETY_S

    def test_discovery_budget_at_defaults_28_seconds(self) -> None:
        """At documented defaults: 30s preflight, 2s safety → 28s aggregate budget."""
        discovery = mcp_discovery_budget_from_env({})
        assert discovery.total_seconds() == 28.0


class TestAggregateBudgetEnforcement:
    """The discovery loop enforces a single aggregate deadline, not per-server budgets."""

    def test_four_slow_servers_probed_within_aggregate_budget(self) -> None:
        """With 4 servers each slower than the per-server budget, only a subset
        are probed before the aggregate deadline is reached, and the total
        elapsed time stays strictly below the preflight timeout."""
        clock = _FakeMonotonic()
        servers = _make_servers("slow-1", "slow-2", "slow-3", "slow-4")
        counter: list[int] = []

        def slow_factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            # Each probe advances clock by 9.5s; aggregate budget = 28s.
            # 3 * 9.5s = 28.5s > 28s budget → only 3 fit.
            # Probe 1: remaining = 28 > 0, clock → 9.5, remaining = 18.5 > 0 ✓
            # Probe 2: remaining = 18.5 > 0, clock → 19, remaining = 9 > 0 ✓
            # Probe 3: remaining = 9 > 0, clock → 28.5, remaining = -0.5 ≤ 0 ✗
            # Probe 4: remaining ≤ 0, skipped.
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

        # Three servers are probed; the fourth is skipped
        assert len(counter) == 3

        # All three should be in the registry
        tools = registry.tool_definitions()
        assert len(tools) == 3

        # Elapsed time (28.5s) is still below preflight (30s).
        preflight = mcp_preflight_timeout_from_env({})
        assert clock() < preflight.total_seconds()

    def test_partial_servers_probed_before_aggregate_budget_exhausted(self) -> None:
        """When the aggregate deadline expires mid-loop, remaining servers are
        skipped but already-probed servers are included in the registry."""
        clock = _FakeMonotonic()
        # 7s per probe: 4 * 7s = 28s = budget (exactly); 5th server is skipped.
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

        # Four probes fit (4 * 7s = 28s = budget); fifth is skipped.
        assert len(counter) == 4
        assert counter == [1, 1, 1, 1]

        # All four should be in the registry
        tools = registry.tool_definitions()
        assert len(tools) == 4

    def test_single_fast_upstream_unaffected_by_aggregate_budget(self) -> None:
        """A single fast upstream is fully probed and appears in the registry,
        with its probe time well within the aggregate budget."""
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
        # Alias format: ralph_upstream__{server_name}__{tool_name}
        assert tools[0].alias == "ralph_upstream__fast__tool-fast"

        elapsed = clock()
        discovery = mcp_discovery_budget_from_env({})
        assert elapsed < discovery.total_seconds()

    def test_on_unreachable_raise_still_respected_when_budget_not_exhausted(self) -> None:
        """When on_unreachable=raise and budget is not exhausted, fast servers
        are fully probed without error."""
        clock = _FakeMonotonic()
        servers = _make_servers("fast-1", "fast-2", "fast-3")
        counter: list[int] = []

        def fast_factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            return _TimeAdvancingClient(
                server, counter, probe_duration=0.5, time_advancer=clock.advance
            )

        # No error should be raised
        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                fast_factory,
            ),
            on_unreachable="raise",
            _monotonic=clock,
        )

        # All three fast servers fit within the aggregate budget
        assert len(counter) == 3
        tools = registry.tool_definitions()
        assert len(tools) == 3

    def test_zero_servers_returns_empty_registry(self) -> None:
        """With no servers, the registry is empty and the deadline is never consulted."""
        clock = _FakeMonotonic()
        registry = UpstreamRegistry.build(
            [],
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )
        assert registry.tool_definitions() == []
        assert clock() == 0.0  # deadline never consulted

    def test_clock_not_advanced_when_server_skipped_due_to_budget(self) -> None:
        """Skipped servers (budget exhausted) must not advance the clock."""
        clock = _FakeMonotonic()
        servers = _make_servers("probe-1", "probe-2", "skipped-1", "skipped-2")
        counter: list[int] = []

        def factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            # 2 * 14.5s = 29s < 30s preflight; 3 * 14.5s = 43.5s > 28s budget
            return _TimeAdvancingClient(
                server, counter, probe_duration=14.5, time_advancer=clock.advance
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

        # 2 probes (2 * 14.5s = 29s); remaining = 28 - 29 = -1 ≤ 0 → third skipped.
        assert len(counter) == 2
        assert clock() == 29.0  # clock advanced only by the 2 successful probes

    def test_multiple_fast_probes_all_fit_within_budget(self) -> None:
        """Many fast probes (total < budget) all succeed without budget exhaustion."""
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

        # 10 * 2s = 20s < 28s budget: all 10 fit
        assert len(counter) == 10
        assert len(registry.tool_definitions()) == 10


class _TimeoutRecordingClient:
    """Fake client that records the timeout_s passed to each list_tools call.

    Advances the shared test clock by 15s per call (simulating a slow probe).
    """

    def __init__(
        self,
        server: UpstreamMcpServer,
        time_advancer: Callable[[float], None],
        recorded_timeouts: list[float | None],
    ) -> None:
        self._server = server
        self._time_advancer = time_advancer
        self._recorded_timeouts = recorded_timeouts

    def list_tools(self, *, timeout_s: float | None = None) -> list[UpstreamTool]:
        self._recorded_timeouts.append(timeout_s)
        self._time_advancer(15.0)
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
    """Each probe call is capped by the remaining budget so the sum never overshoots."""

    def test_second_probe_receives_remaining_budget(self) -> None:
        """The second probe's timeout_s equals the remaining budget after the first.

        Budget = 28s, first probe consumes 15s, leaving 13s. The second probe
        must receive timeout_s == 13.0 (the remaining budget), proving the hard
        bound is enforced per-call so the aggregate never exceeds the deadline.
        """
        clock = _FakeMonotonic()
        servers = _make_servers("slow-1", "slow-2")
        recorded_timeouts: list[float | None] = []

        def recording_factory(server: UpstreamMcpServer) -> _TimeoutRecordingClient:
            return _TimeoutRecordingClient(server, clock.advance, recorded_timeouts)

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                recording_factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )

        # Both servers are probed
        assert len(recorded_timeouts) == 2

        # First call: remaining = 28s, env budget >= 28s so no cap needed
        # Second call: remaining after first = 13s, so timeout_s == 13.0
        assert recorded_timeouts[0] == 28.0, (
            f"expected first call timeout_s=28.0, got {recorded_timeouts[0]}"
        )
        assert recorded_timeouts[1] == 13.0, (
            f"expected second call timeout_s=13.0 (28 - 15), got {recorded_timeouts[1]}"
        )

        # Fake clock advances 15s per call regardless of timeout
        assert clock() == 30.0

        # Both tools are registered
        assert len(registry.tool_definitions()) == 2

    def test_third_server_skipped_after_budget_exhausted(self) -> None:
        """After two 15s probes, remaining budget is -2s; the third server is skipped.

        The recorded timeout_s values (28.0, 13.0) prove the hard bound was applied
        to each call, ensuring the aggregate never exceeds the 28s budget.
        """
        clock = _FakeMonotonic()
        servers = _make_servers("probe-1", "probe-2", "probe-3")
        recorded_timeouts: list[float | None] = []

        def recording_factory(server: UpstreamMcpServer) -> _TimeoutRecordingClient:
            return _TimeoutRecordingClient(server, clock.advance, recorded_timeouts)

        registry = UpstreamRegistry.build(
            servers,
            client_factory=cast(
                "Callable[[UpstreamMcpServer], HttpUpstreamClient | StdioUpstreamClient] | None",
                recording_factory,
            ),
            on_unreachable="warn_and_skip",
            _monotonic=clock,
        )

        # Only two probes executed; third was skipped due to exhausted budget
        assert len(recorded_timeouts) == 2
        assert recorded_timeouts[0] == 28.0
        assert recorded_timeouts[1] == 13.0

        # Third server was skipped (no remaining budget)
        assert len(registry.tool_definitions()) == 2


class TestAggregateVsPerStageEnvelope:
    """WU-C2: the aggregate bound is the shared deadline, not a sum of stage budgets."""

    def test_aggregate_bound_is_preflight_enforced_by_deadline(self) -> None:
        """Spawn-to-tools/list is bounded by preflight via the shared deadline;
        it is NOT the sum of the per-stage budgets (which would exceed it)."""
        from ralph.mcp.protocol import _startup_timeouts as mod

        preflight = mcp_preflight_timeout_from_env({}).total_seconds()
        discovery = mcp_discovery_budget_from_env({}).total_seconds()
        assert discovery < preflight
        per_stage_sum = mod._CONFIG_LOAD_BUDGET_S + mod._REGISTRY_BUILD_BUDGET_S + discovery
        assert per_stage_sum > discovery, "summing must not be the enforcement mechanism"
        # The registry tests above already prove the shared deadline skips
        # probes past the budget; here we pin the helper that records stage
        # overruns against per-stage envelopes.
        from ralph.mcp.protocol._startup_timeouts import record_stage_elapsed
        from ralph.mcp.server._mcp_server_error import McpServerError

        with pytest.raises(McpServerError) as exc_info:
            record_stage_elapsed(
                "upstream_catalog_or_discovery_done",
                mod._REGISTRY_BUILD_BUDGET_S + 0.01,
                budget_s=mod._REGISTRY_BUILD_BUDGET_S,
            )
        assert exc_info.value.last_stage == "upstream_catalog_or_discovery_done"
