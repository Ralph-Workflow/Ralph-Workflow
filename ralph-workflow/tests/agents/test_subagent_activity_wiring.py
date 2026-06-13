"""Black-box tests for the OpenCodeExecutionStrategy subagent activity shim.

The OpenCodeExecutionStrategy owns the child_liveness registry; the
``record_subagent_work`` shim is the watchdog's evidence surface. The
two concerns exercised here:

1. **Sink invoked on CHILD_PROGRESS / CHILD_HEARTBEAT**: a strategy
   constructed with a ``subagent_activity_sink`` callable invokes the
   sink when observe_line sees a child_progress / child_heartbeat
   / progress / heartbeat / tool_call / writing_artifact event. A
   fresh ChildLivenessRegistry is also constructed so the test
   exercises both the sink path and the registry path.

2. **Sink NOT invoked on non-child lines**: a strategy constructed
   with a sink does NOT invoke the sink when observe_line sees a
   regular output_line, lifecycle event, or any line that is not a
   child-lifecycle event. The sink is scoped to the two 'demonstrable
   work' kinds (CHILD_PROGRESS, CHILD_HEARTBEAT) so unrelated agent
   output does not pollute the activity channel.

All tests use FakeClock where applicable, no real I/O, no real
subprocess. Total wall-clock for the file is well under 1s.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.process.child_liveness import ChildLivenessRegistry

if TYPE_CHECKING:
    from collections.abc import Callable

_PROGRESS_LINE = json.dumps(
    {"type": "child_progress", "child_id": "child-A", "phase": "reading_file"}
)
_HEARTBEAT_LINE = json.dumps({"type": "child_heartbeat", "child_id": "child-A"})
_TERMINAL_LINE = json.dumps({"type": "child_complete", "child_id": "child-A"})
_SPAWN_LINE = json.dumps({"type": "child_started", "child_id": "child-A", "pid": 12345})
_TOOL_CALL_LINE = json.dumps({"type": "tool_call", "child_id": "child-A", "phase": "tool_use"})
_WRITING_ARTIFACT_LINE = json.dumps(
    {"type": "writing_artifact", "child_id": "child-A", "phase": "writing"}
)
_NON_CHILD_LINE = "regular agent output that is not a child event"
_LIFECYCLE_LINE = json.dumps({"type": "message_start"})


def _build_strategy(
    sink: Callable[[str], None],
) -> tuple[OpenCodeExecutionStrategy, ChildLivenessRegistry]:
    """Build a strategy with a sink and a fresh registry."""
    registry = ChildLivenessRegistry(
        progress_ttl=45.0,
        heartbeat_ttl=15.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
    )
    strategy = OpenCodeExecutionStrategy(
        label_scope="test-scope",
        registry=registry,
        subagent_activity_sink=sink,
    )
    return strategy, registry


# ---------------------------------------------------------------------------
# (d) observe_line invokes sink on child_progress / child_heartbeat
# ---------------------------------------------------------------------------


def test_opencode_observe_line_invokes_sink_on_child_progress() -> None:
    """A strategy with an explicit sink invokes it on a child_progress
    line so the per-run watchdog can refresh its subagent channel
    evidence. The registry is also updated, so the sink is additive
    on top of the existing child_liveness tracking.

    Note: the spawn line (``CHILD_PROCESS`` kind) is NOT in the
    sink's filter set, so the sink is called ONLY for the
    progress/heartbeat events. The registry, however, is updated
    on the spawn line so the child can be tracked.
    """
    sink_calls: list[str] = []

    def sink(line: str) -> None:
        sink_calls.append(line)

    strategy, registry = _build_strategy(sink)
    # Register the child first so the registry can record the
    # subsequent progress signal. The spawn line does NOT invoke
    # the sink (CHILD_PROCESS is not a "demonstrable work" kind).
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_PROGRESS_LINE)
    assert sink_calls == [_PROGRESS_LINE], (
        f"expected sink to be called with the progress line only, got {sink_calls}"
    )
    # Registry was also updated.
    assert registry.has_records("agent:test-scope:")


def test_opencode_observe_line_invokes_sink_on_heartbeat() -> None:
    """Heartbeat signals also invoke the sink (live, demonstrable work)."""
    sink_calls: list[str] = []

    def sink(line: str) -> None:
        sink_calls.append(line)

    strategy, _registry = _build_strategy(sink)
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_HEARTBEAT_LINE)
    assert sink_calls == [_HEARTBEAT_LINE]


def test_opencode_observe_line_invokes_sink_on_tool_call() -> None:
    """``tool_call`` and ``writing_artifact`` events (also in the
    CHILD_PROGRESS family) invoke the sink.
    """
    sink_calls: list[str] = []

    def sink(line: str) -> None:
        sink_calls.append(line)

    strategy, _registry = _build_strategy(sink)
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_TOOL_CALL_LINE)
    strategy.observe_line(_WRITING_ARTIFACT_LINE)
    assert sink_calls == [_TOOL_CALL_LINE, _WRITING_ARTIFACT_LINE]


# ---------------------------------------------------------------------------
# (e) observe_line does NOT invoke sink on non-child lines
# ---------------------------------------------------------------------------


def test_opencode_observe_line_does_not_invoke_sink_on_non_child_line() -> None:
    """The sink is scoped to CHILD_PROGRESS / CHILD_HEARTBEAT only.
    Regular agent output and lifecycle frames do NOT invoke the sink.
    """
    sink_calls: list[str] = []

    def sink(line: str) -> None:
        sink_calls.append(line)

    strategy, _registry = _build_strategy(sink)
    strategy.observe_line(_NON_CHILD_LINE)
    strategy.observe_line(_LIFECYCLE_LINE)
    assert sink_calls == [], f"sink should NOT be called for non-child lines, got {sink_calls}"


def test_opencode_observe_line_does_not_invoke_sink_on_spawn_or_terminal() -> None:
    """Spawn and terminal events are NOT forward progress. A
    child_started event is just OS-level evidence the child was
    launched; a child_complete event means the child is no longer
    running. Neither should invoke the sink.
    """
    sink_calls: list[str] = []

    def sink(line: str) -> None:
        sink_calls.append(line)

    strategy, _registry = _build_strategy(sink)
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_TERMINAL_LINE)
    assert sink_calls == [], (
        f"sink should NOT be called for spawn/terminal events, got {sink_calls}"
    )


# ---------------------------------------------------------------------------
# (f) Sink is optional: a strategy without a sink works fine
# ---------------------------------------------------------------------------


def test_opencode_observe_line_without_sink_does_not_raise() -> None:
    """A strategy constructed WITHOUT a subagent_activity_sink
    argument must still process lines correctly (backward compat
    with the pre-feature call sites).
    """
    registry = ChildLivenessRegistry(
        progress_ttl=45.0,
        heartbeat_ttl=15.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
    )
    strategy = OpenCodeExecutionStrategy(
        label_scope="test-scope",
        registry=registry,
    )
    # No sink -> no exception, registry still updated when the child
    # is registered first via a spawn line.
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_PROGRESS_LINE)
    assert registry.has_records("agent:test-scope:")


# ---------------------------------------------------------------------------
# (g) Sink exception does not break the line loop
# ---------------------------------------------------------------------------


def test_opencode_observe_line_sink_exception_does_not_break_loop() -> None:
    """A buggy sink that raises must not break the line loop or
    corrupt the registry. The sink is invoked in a try/except so
    subsequent lines are still processed.
    """

    def bad_sink(line: str) -> None:
        raise RuntimeError("buggy sink")

    strategy, registry = _build_strategy(bad_sink)
    strategy.observe_line(_SPAWN_LINE)  # sink raises on spawn, loop survives
    # A follow-up line is still processed.
    strategy.observe_line(_HEARTBEAT_LINE)
    # Registry is still updated.
    assert registry.has_records("agent:test-scope:")
