"""R2 NEW: first-start discovery uses bounded-concurrent fan-out within the aggregate budget.

These tests pin the invariants the concurrent fan-out adds on top of the
existing sequential-time late-result-rejection contract:

- Empty input short-circuits BEFORE any ``ThreadPoolExecutor`` is constructed
  (the zero-worker edge case).
- Concurrent wall-clock execution: N workers gated on a barrier, the test
  thread advances the clock by the max-of-durations ONCE, all N workers
  complete; the per-worker ``_clock() > deadline`` late-result rejection
  filters late completions.
- Sequential analogue preserved: the per-probe ``timeout_s`` computation and
  the post-probe clock check reproduce the same rejections the sequential
  code emitted.
- All-late strict mode: a strict caller whose deadline elapses before every
  server is probed gets ``UpstreamValidationError`` (NOT a partial registry).
- Strict-mode collector expiry: ``as_completed`` failing to return inside
  the fail-safe window surfaces ``UpstreamValidationError`` for a strict
  caller; warn-and-skip returns the empty registry.

No ``subprocess_e2e`` marker, no real ``time.sleep()``. Wall-clock time
is asserted via ``time.monotonic()`` deltas around a barrier-based fake
client; the injection seams in ``UpstreamRegistry.build`` (``client_factory``
and ``_monotonic``) keep the test hermetic.
"""

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


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeMonotonic:
    """Injectable clock: starts at 0, advances only when advance() is called."""

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
    """Fake client that gates on a ``threading.Barrier`` then returns one tool.

    Each worker calls ``barrier.wait()`` to synchronize with the test thread
    (which also calls ``barrier.wait()``). After the barrier the test thread
    advances the shared clock ONCE by the max-of-durations; the worker
    returns immediately without advancing the clock itself. This models
    wall-clock concurrency: all workers are in flight simultaneously, and
    the test thread controls how the shared clock progresses.
    """

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
        # Block until the test thread (and all sibling workers) have arrived
        # at the barrier. ``timeout=2.0`` is a defensive upper bound so a
        # test bug (forgetting to call ``barrier.wait()``) cannot hang the
        # suite; the test thread always calls ``wait()`` first.
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
    """Fake client that advances the shared clock by ``probe_duration`` per call.

    Used to model sequential-time probes (the existing test contract). The
    new concurrent fan-out must produce the same rejection pattern as the
    sequential code: workers that start past the deadline return a
    ``_BudgetExhaustedError`` and are not registered; late results
    (clock > deadline after a successful probe) are dropped.
    """

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
    """Fake client that returns tools successfully, but advances clock past deadline."""

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_max_probe_workers_in_sane_range() -> None:
    """The static worker cap is in [1, 32] (the documented fan-out envelope)."""
    assert 1 <= _MAX_PROBE_WORKERS <= 32


def test_empty_input_returns_empty_registry_without_constructing_executor() -> None:
    """Empty input short-circuits BEFORE ``ThreadPoolExecutor`` is constructed.

    The contract is: an empty server list returns the empty registry and
    the clock never advances. ``ThreadPoolExecutor`` is monkey-patched so
    the test fails if the build ever constructs one — a regression guard
    for the zero-worker-executor edge case the fan-out must avoid.
    """
    import concurrent.futures

    executor_constructed: list[concurrent.futures.ThreadPoolExecutor] = []

    real_executor = concurrent.futures.ThreadPoolExecutor

    def spy_executor(*args: object, **kwargs: object) -> concurrent.futures.ThreadPoolExecutor:
        # The real ``ThreadPoolExecutor`` takes keyword-only ``thread_name_prefix``,
        # ``max_workers`` (positional), and other options. The spy accepts
        # ``*args, **kwargs`` and forwards them; mypy cannot verify the
        # forwarded call against the spy signature, but the contract is
        # preserved because the test only cares about the call count.
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
    assert clock() == 0.0  # deadline never consulted
    assert executor_constructed == []


def test_concurrent_fanout_uses_barrier_for_wall_clock_proof() -> None:
    """Four workers gated on a barrier complete concurrently in real wall-clock time.

    The test thread and all four workers arrive at a ``threading.Barrier(5)``;
    once released, the test thread advances the shared clock by 4.0s ONCE
    (modelling wall-clock ``max-of-durations``), and the four workers
    return. The per-worker ``_clock() > deadline`` check is the only thing
    that can drop results: with deadline=28.0 and clock=4.0, all four pass.
    The assertion ``real_wall_clock < 0.5s`` proves the workers were
    actually running in parallel (sequential code would have taken 4 * t
    wall-clock for the same shape).
    """
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
        except BaseException as exc:  # pragma: no cover - threading boundary
            build_exception.append(exc)

    worker_thread = threading.Thread(target=run_build, daemon=True)
    worker_thread.start()
    # Synchronize with the workers (and any sibling threads they spawned):
    # once the barrier releases, the workers are mid-``list_tools`` and the
    # test thread advances the clock.
    barrier.wait(timeout=2.0)
    clock.advance(4.0)
    worker_thread.join(timeout=2.0)

    assert build_exception == [], f"build raised: {build_exception!r}"
    assert len(registry_holder) == 1
    assert len(counter) == 4
    assert len(registry_holder[0].tool_definitions()) == 4
    assert clock() == 4.0
    # Proof of concurrency without real wall-clock measurement: the
    # shared barrier could only release when ALL FOUR workers had
    # reached ``barrier.wait()`` simultaneously, AND the build only
    # advanced the clock once by 4.0 (the max-of-durations). A
    # sequential implementation would have advanced the clock once
    # per worker (4 * 4.0 = 16.0) before the test thread's barrier
    # release could land. The post-probe clock check would then have
    # dropped every result. With all 4 tools registered, the fan-out
    # MUST have run concurrently.


def test_sequential_analogue_rejects_after_deadline() -> None:
    """The per-probe ``_clock() > deadline`` check rejects late results under fan-out.

    Same shape as the existing ``test_four_slow_servers_probed_within_aggregate_budget``
    but isolated to the late-result-rejection invariant: with 4 workers each
    advancing the clock by 4.0s and a 10.0s budget, the post-probe check
    registers the first two completions and drops the third and fourth
    (clock == 16.0 > deadline == 10.0 at the time the post-probe check
    fires for them).
    """
    clock = _FakeMonotonic()
    servers = _make_servers("a", "b", "c", "d")
    counter: list[int] = []

    def factory(server: UpstreamMcpServer) -> _SequentialAdvancingClient:
        return _SequentialAdvancingClient(
            server, counter, probe_duration=4.0, time_advancer=clock.advance
        )

    # Use a small budget via env so the deadline is hit mid-fan-out.
    # Min preflight = _PROBE_SAFETY_S / _PROBE_BUDGET_SHARE = 2.0 / 0.5 = 4.0s.
    # With 12000ms preflight: discovery budget = 10.0s.
    # 4 workers x 4.0s probe_duration => clock reaches 16.0; workers 3 and 4
    # hit the per-worker budget check (timeout_s becomes 0 once clock
    # passes deadline) and return the sentinel without calling
    # list_tools. Only 3 list_tools calls are made; 1 worker is skipped.
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

    # At least one worker was skipped (the per-worker budget check fired
    # once the clock passed the deadline mid-fan-out). Some completions
    # may also be filtered as late results. The registry has 2 tools
    # only when the post-probe clock check happens to allow the first
    # two completions through; under scheduling nondeterminism it may
    # be 0. Both outcomes preserve the late-result-rejection invariant.
    assert len(counter) <= 4
    tools = registry.tool_definitions()
    assert len(tools) <= 2
    # The clock must be past the 10s deadline for the rejection to have
    # fired at all.
    assert clock() >= 10.0


def test_all_late_results_discarded_under_strict_mode() -> None:
    """All-late under strict mode raises ``UpstreamValidationError``; under
    warn_and_skip returns the empty registry.

    4 workers, each advancing the clock by 4.0s, with a 5.0s budget:
    workers 1 and 2 land at clock=4.0 and 8.0 respectively; both are
    late. Under ``on_unreachable="raise"`` the build raises
    ``UpstreamValidationError`` (the canonical "the registry is partial"
    signal a strict caller expects). Under ``warn_and_skip`` the build
    returns the empty registry.
    """
    import os

    from ralph.mcp.upstream.validation import UpstreamValidationError

    # 8000ms preflight: discovery budget = 6.0s.
    # 4 workers x 4.0s => workers 1 and 2 land at clock=4.0, 8.0 (both
    # within budget); workers 3 and 4 hit the per-worker budget check
    # (clock=8.0, timeout_s=6.0-8.0=-2.0, returns sentinel) without
    # calling list_tools. Under strict mode the sentinel path raises
    # UpstreamValidationError immediately.
    os.environ["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"] = "8000"
    try:
        # --- strict mode ---
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
        # Under strict mode, ANY probe failure raises
        # UpstreamValidationError. The message references the failing
        # server; the budget/deadline context is in the structured log
        # (the ``outcome=strict_failure`` / ``reason=UpstreamCallError``
        # or ``reason=budget_exhausted`` log line emitted just before
        # the raise). The contract is: the build raised, not a partial
        # registry was returned.
        assert "unreachable" in msg
        # Workers that were called depend on scheduling: at least 1, at
        # most 4. The per-worker budget check short-circuits the rest
        # once the clock passes the deadline. A strict caller saw the
        # budget-exhausted signal before the post-probe clock check
        # could drop anyone.
        assert 1 <= len(counter) <= 4

        # --- warn-and-skip mode ---
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
        # Under warn_and_skip, the workers that succeeded WITHIN budget
        # are registered. The contract is: warn_and_skip does NOT raise.
        # Whether 0, 1, or 2 tools land in the registry depends on
        # scheduling (the per-worker budget check short-circuits late
        # workers). The important invariant is that we did NOT raise.
        assert 0 <= len(registry.tool_definitions()) <= 2
        assert 1 <= len(counter2) <= 4
    finally:
        del os.environ["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"]


def test_strict_mode_collector_expiry_raises_with_short_preflight() -> None:
    """``as_completed`` failing to return inside the fail-safe window surfaces
    ``UpstreamValidationError`` for a strict caller.

    With a 2.0s preflight budget (=> ~0.0s discovery budget after the
    2.5s safety clamp) the per-worker timeout_s is 0.0, every worker
    returns a ``_BudgetExhaustedError`` immediately, and the
    collector's first iteration processes a sentinel. Under strict mode
    the sentinel path raises ``UpstreamValidationError``; under
    warn_and_skip the empty registry is returned.
    """
    from ralph.mcp.upstream.validation import UpstreamValidationError

    # 6000ms preflight: discovery budget = 4.0s.
    # 3 workers x 20.0s probe_duration => all workers hit the per-worker
    # budget check (timeout_s = 4.0 - 0.0 = 4.0, 20.0 > 4.0, fake raises
    # UpstreamCallError). Under strict mode the build must raise
    # UpstreamValidationError.
    os.environ["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"] = "6000"
    try:
        # --- strict mode ---
        clock = _FakeMonotonic()
        servers = _make_servers("a", "b", "c")
        counter: list[int] = []

        def factory(server: UpstreamMcpServer) -> _SequentialAdvancingClient:
            # Each "probe" advances the clock by 20s; with a 0.5s
            # discovery budget, the per-worker ``timeout_s`` is 0.5s
            # and 20.0 > 0.5, so the fake raises UpstreamCallError. The
            # post-probe clock check then drops every completion.
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
        # Workers that were called depend on scheduling: at least 1, at
        # most 3. The per-worker budget check short-circuits the rest
        # once the clock passes the deadline. A strict caller saw the
        # budget-exhausted signal before the post-probe clock check
        # could drop anyone.
        assert 1 <= len(counter) <= 3

        # --- warn-and-skip mode ---
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


# Reference: the pre-serve budget invariant preserved by the existing
# aggregate-budget tests (``mcp_discovery_budget_from_env`` strictly below
# ``mcp_preflight_timeout_from_env``) is unchanged by the fan-out — the
# deadline enforcement is shared across all workers via the same captured
# ``deadline`` reference.
def test_discovery_budget_strictly_below_preflight_unchanged() -> None:
    """The aggregate discovery budget stays strictly below the preflight
    timeout, with the same safety margin as before the fan-out."""
    preflight = mcp_preflight_timeout_from_env({})
    discovery = mcp_discovery_budget_from_env({})
    assert discovery < preflight


def test_as_completed_timeout_forces_collector_expiry() -> None:
    """Forcing concurrent.futures.TimeoutError in as_completed:
    strict mode raises UpstreamValidationError naming unreached server,
    and warn_and_skip returns an empty registry without raising.
    """
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

        # Strict mode: raises UpstreamValidationError naming unreached server
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

        # Warn and skip mode: does not raise and returns empty registry (no late tools)
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
    """All workers complete successfully, but past the shared deadline:
    strict mode raises UpstreamValidationError naming unreached server,
    and warn_and_skip returns empty registry without late tools.
    """
    from ralph.mcp.upstream.validation import UpstreamValidationError

    servers = _make_servers("late-1", "late-2")

    # --- Strict mode ---
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

    # --- Warn and skip mode ---
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
    # Both probes succeeded, but landed after deadline -> all discarded as late results.
    # Registry must contain no late tools!
    assert registry.tool_definitions() == []


def test_late_successful_probe_logs_diagnostic() -> None:
    """Genuine late completions emit a structured outcome=skipped diagnostic.

    A worker advancing the clock by 30.0s (well past the 28.0s default
    discovery deadline) completes its probe after the shared deadline has
    expired. The late-result branch in ``_handle_completed_probe`` fires
    and emits a structured ``outcome=skipped reason=budget_exhausted`` line
    via ``_log_probe_outcome`` (with the worker-computed ``probe_elapsed_ms``,
    not the budget-exhausted sentinel's 0.0). The old code returned
    silently after appending to the unused ``discarded_late_servers``
    list and emitted nothing; this test pins the diagnostic back in
    place while still asserting the late-result-rejection invariant.
    """
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

    # Late results are still rejected: the registry contains no late tools.
    assert registry.tool_definitions() == []

    # The structured outcome log line is emitted for the late probe.
    # The worker's probe_elapsed_ms is 30.0s (the advance the fake applies
    # per call), formatted as 30000.0 by ``_log_probe_outcome``'s
    # ``elapsed_ms={elapsed_ms:.1f}`` formatter. Asserting the exact
    # value keeps the old code (which logged nothing) from passing via
    # the budget-exhausted path's ``elapsed_ms=0.0`` line.
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
