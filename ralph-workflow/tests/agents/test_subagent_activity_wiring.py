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


def test_opencode_observe_line_invokes_sink_on_child_progress() -> None:
    sink_calls: list[str] = []

    def sink(line: str) -> None:
        sink_calls.append(line)

    strategy, registry = _build_strategy(sink)
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_PROGRESS_LINE)
    assert sink_calls == [_PROGRESS_LINE], (
        f"expected sink to be called with the progress line only, got {sink_calls}"
    )
    assert registry.has_records("agent:test-scope:")


def test_opencode_observe_line_invokes_sink_on_heartbeat() -> None:
    sink_calls: list[str] = []

    def sink(line: str) -> None:
        sink_calls.append(line)

    strategy, _registry = _build_strategy(sink)
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_HEARTBEAT_LINE)
    assert sink_calls == [_HEARTBEAT_LINE]


def test_opencode_observe_line_invokes_sink_on_tool_call() -> None:
    sink_calls: list[str] = []

    def sink(line: str) -> None:
        sink_calls.append(line)

    strategy, _registry = _build_strategy(sink)
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_TOOL_CALL_LINE)
    strategy.observe_line(_WRITING_ARTIFACT_LINE)
    assert sink_calls == [_TOOL_CALL_LINE, _WRITING_ARTIFACT_LINE]


def test_opencode_observe_line_does_not_invoke_sink_on_non_child_line() -> None:
    sink_calls: list[str] = []

    def sink(line: str) -> None:
        sink_calls.append(line)

    strategy, _registry = _build_strategy(sink)
    strategy.observe_line(_NON_CHILD_LINE)
    strategy.observe_line(_LIFECYCLE_LINE)
    assert sink_calls == [], f"sink should NOT be called for non-child lines, got {sink_calls}"


def test_opencode_observe_line_does_not_invoke_sink_on_spawn_or_terminal() -> None:
    sink_calls: list[str] = []

    def sink(line: str) -> None:
        sink_calls.append(line)

    strategy, _registry = _build_strategy(sink)
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_TERMINAL_LINE)
    assert sink_calls == [], (
        f"sink should NOT be called for spawn/terminal events, got {sink_calls}"
    )


def test_opencode_observe_line_without_sink_does_not_raise() -> None:
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
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_PROGRESS_LINE)
    assert registry.has_records("agent:test-scope:")


def test_opencode_observe_line_sink_exception_does_not_break_loop() -> None:

    def bad_sink(line: str) -> None:
        raise RuntimeError("buggy sink")

    strategy, registry = _build_strategy(bad_sink)
    strategy.observe_line(_SPAWN_LINE)
    strategy.observe_line(_HEARTBEAT_LINE)
    assert registry.has_records("agent:test-scope:")
