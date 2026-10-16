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

    def list_tools(self) -> list[UpstreamTool]:
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

    def list_tools(self) -> list[UpstreamTool]:
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
            return _TimeAdvancingClient(server, counter, probe_duration=9.5, time_advancer=clock.advance)

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
            return _TimeAdvancingClient(server, counter, probe_duration=7.0, time_advancer=clock.advance)

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
            return _TimeAdvancingClient(server, counter, probe_duration=0.5, time_advancer=clock.advance)

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
            return _TimeAdvancingClient(server, counter, probe_duration=0.5, time_advancer=clock.advance)

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
            return _TimeAdvancingClient(server, counter, probe_duration=14.5, time_advancer=clock.advance)

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
            return _TimeAdvancingClient(server, counter, probe_duration=2.0, time_advancer=clock.advance)

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
