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

import io
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
        elapsed time stays strictly at or below the aggregate budget."""
        clock = _FakeMonotonic()
        servers = _make_servers("slow-1", "slow-2", "slow-3", "slow-4")
        counter: list[int] = []

        def slow_factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            # Each probe would take 9.5s; aggregate budget = 28s.
            # Probe 1: remaining = 28.0 > 0, timeout_s = 28.0. Probe takes 9.5s -> clock = 9.5s.
            # Probe 2: remaining = 18.5 > 0, timeout_s = 18.5. Probe takes 9.5s -> clock = 19.0s.
            # Probe 3: remaining = 9.0 > 0, timeout_s = 9.0. Probe needs 9.5s > 9.0s, so it times out
            #          after 9.0s -> clock = 28.0s. It raises UpstreamCallError and is skipped.
            # Probe 4: remaining = 28.0 - 28.0 = 0.0 <= 0 -> skipped before probe.
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

        # Three servers are probed; the fourth is skipped before probe
        assert len(counter) == 3

        # Only the two that finished within budget are in the registry
        tools = registry.tool_definitions()
        assert len(tools) == 2

        # Elapsed time is exactly 28.0s, strictly at or below the 28s aggregate budget
        # and strictly below preflight (30s).
        assert clock() == 28.0
        discovery = mcp_discovery_budget_from_env({})
        assert clock() <= discovery.total_seconds()
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
            # 2 * 14.0s = 28.0s = aggregate budget; remaining for 3rd and 4th is 0s
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

        # 2 probes (2 * 14.0s = 28.0s); remaining = 28 - 28 = 0 ≤ 0 → third and fourth skipped.
        assert len(counter) == 2
        assert clock() == 28.0  # clock advanced only by the 2 successful probes

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

    Advances the shared test clock up to probe_duration (capped by timeout_s).
    """

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

        # Clock advances 15s on first probe, 13s on second probe (capped at timeout_s=13.0) -> total 28.0s
        assert clock() == 28.0
        assert clock() <= 28.0

        # First tool registered; second timed out and skipped under warn_and_skip
        assert len(registry.tool_definitions()) == 1

    def test_second_probe_succeeds_when_within_remaining_budget(self) -> None:
        """When the second probe completes within its remaining budget, both succeed."""
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
        """After two probes consume the entire 28s budget, the third server is skipped.

        The recorded timeout_s values (28.0, 13.0) prove the hard bound was applied
        to each call, ensuring the aggregate never exceeds the 28s budget.
        """
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

        # Only two probes executed; third was skipped due to exhausted budget
        assert len(recorded_timeouts) == 2
        assert recorded_timeouts[0] == 28.0
        assert recorded_timeouts[1] == 13.0

        # Clock never exceeds the 28s aggregate budget
        assert clock() == 28.0
        assert clock() <= 28.0

        # Third server was skipped (no remaining budget); second timed out
        assert len(registry.tool_definitions()) == 1


class TestStrictModeFailsClosedOnBudgetExhaustion:
    """R2 strict-fail-closed: exhausted aggregate budget raises, never silently
    returns an incomplete registry. The previous bug returned an empty
    registry under strict mode and the runtime happily bound it as if every
    server were unreachable. The new contract: a strict caller asking for
    fail-closed gets fail-closed.
    """

    def test_strict_mode_raises_when_budget_exhausted_before_first_server(
        self,
    ) -> None:
        """Under ``on_unreachable='raise'`` the registry must raise a typed
        ``UpstreamValidationError`` when the aggregate deadline expires
        before any server is probed, instead of returning an empty
        registry that the runtime would happily bind.
        """
        from ralph.mcp.upstream.validation import UpstreamValidationError

        # Fake clock: first call (deadline calc) returns 0.0, second call
        # (per-server remaining check) returns 29.0, well past the 28s
        # aggregate budget. The factory must NOT be invoked.
        call_count = [0]

        def fake_clock() -> float:
            call_count[0] += 1
            if call_count[0] == 1:
                return 0.0  # deadline = 0.0 + 28 = 28.0
            return 29.0  # remaining = 28.0 - 29.0 = -1.0

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
        """The non-strict ``warn_and_skip`` branch keeps its legacy behavior:
        an exhausted budget logs and skips the server, returning whatever
        registry was already built. This guards the agent-upstream branch
        in the runtime, which depends on warn-and-skip to tolerate slow
        third-party servers without aborting the session.
        """
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

        # Empty registry: budget exhausted before probe, warn-and-skip
        # branch did NOT raise.
        assert registry.tool_definitions() == []
        assert factory_calls == []

    def test_strict_mode_partial_success_then_budget_exhausted_raises(
        self,
    ) -> None:
        """A strict caller whose first probes succeeded and next was
        skipped due to budget exhaustion must still raise. The earlier
        server tools are not enough; the contract is that the registry
        is either complete or the build fails.
        """
        from ralph.mcp.upstream.validation import UpstreamValidationError

        clock = _FakeMonotonic()
        counter: list[int] = []

        def slow_factory(server: UpstreamMcpServer) -> _TimeAdvancingClient:
            # 14s per probe: 1st probe clock=14 (remaining=14), 2nd probe
            # clock=28 (remaining=0), 3rd server hits the exhausted branch.
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
        assert any(s in str(exc_info.value) for s in ("ok-1", "ok-2", "too-slow"))
        # First two probes ran; third hit the exhausted branch.
        assert len(counter) in (2, 3)

    def test_strict_mode_raises_when_probe_times_out_at_budget_limit(self) -> None:
        """Under on_unreachable='raise', if a server probe times out against the
        remaining discovery budget, UpstreamValidationError is raised immediately."""
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

        # First succeeded (15s), second timed out against 13s remaining budget
        assert len(recorded_timeouts) == 2
        assert recorded_timeouts[0] == 28.0
        assert recorded_timeouts[1] == 13.0
        assert "is unreachable" in str(exc_info.value)
        assert "strict-2" in str(exc_info.value)
        assert clock() == 28.0


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


class _RaisingClient:
    """Fake client that raises UpstreamCallError after advancing the shared clock."""

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
    """R4: both failure paths record elapsed_ms and server identity via structured logs.

    Loguru writes directly to stderr, so these tests use an in-memory StringIO
    sink that loguru outputs into; we then parse the captured text for the
    expected structured fields.
    """

    def test_failed_probe_records_elapsed_and_server_identity(self) -> None:
        """Warn-and-skip branch logs outcome=skipped reason=UpstreamCallError
        with the measured elapsed time and server name."""
        clock = _FakeMonotonic()
        servers = _make_servers("failing-server")
        counter: list[int] = []

        def raising_factory(server: UpstreamMcpServer) -> _RaisingClient:
            # Advance clock by 5s before raising: probe_elapsed_ms ≈ 5000.0
            return _RaisingClient(server, counter, probe_duration=5.0, time_advancer=clock.advance)

        # Capture loguru output to an in-memory sink
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

        # Registry is empty (server was skipped)
        assert registry.tool_definitions() == []

        # Parse the captured log output
        captured = sink.getvalue()
        assert "ralph-mcp upstream probe:" in captured
        assert "server=failing-server" in captured
        assert "outcome=skipped" in captured
        assert "reason=UpstreamCallError" in captured
        # Elapsed time should be approximately 5000.0 ms (5s * 1000)
        assert "elapsed_ms=5000.0" in captured

    def test_strict_failed_probe_records_elapsed_and_server_identity(self) -> None:
        """Strict-raise branch logs outcome=strict_failure BEFORE raising
        UpstreamValidationError, with the same elapsed time and server name fields."""
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

        # Structured ERROR was emitted BEFORE the raise
        captured = sink.getvalue()
        assert "ralph-mcp upstream probe:" in captured
        assert "server=strict-failing-server" in captured
        assert "outcome=strict_failure" in captured
        assert "reason=UpstreamCallError" in captured
        assert "elapsed_ms=3000.0" in captured

    def test_budget_exhausted_skip_records_server_identity(self) -> None:
        """When the aggregate budget is exhausted before a server is reached,
        the skipped server's structured log carries outcome=skipped
        reason=budget_exhausted with that server's identity."""
        counter: list[int] = []

        def fake_clock() -> float:
            # First call: deadline = 0 + 28 = 28.0
            # Second+ calls: 28 - 29 = -1 → exhausted (budget hits before any probe)
            counter.append(1)
            if len(counter) == 1:
                return 0.0  # deadline calculation
            return 29.0  # remaining ≤ 0 → budget exhausted

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

        # Both servers skipped due to exhausted budget (no probe ran at all)
        assert len(registry.tool_definitions()) == 0

        captured = sink.getvalue()
        # The second server's structured skip line must be present
        assert "server=skipped-server" in captured
        assert "outcome=skipped" in captured
        assert "reason=budget_exhausted" in captured
        assert "elapsed_ms=0.0" in captured
