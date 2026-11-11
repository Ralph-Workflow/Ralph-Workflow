from __future__ import annotations

from ralph.agents.execution_state._helpers import parse_opencode_child_id
from ralph.agents.execution_state.opencode_execution_strategy import (
    OpenCodeExecutionStrategy,
)
from ralph.process.child_liveness import ChildLivenessRegistry


def _make_registry() -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=180.0,
        heartbeat_ttl=180.0,
        stale_label_ttl=600.0,
        exit_reconcile=10.0,
    )


def _make_strategy_with_sink(
    registry: ChildLivenessRegistry | None = None,
) -> tuple[OpenCodeExecutionStrategy, list[str], ChildLivenessRegistry | None]:
    sink_calls: list[str] = []

    def _sink(line: str) -> None:
        sink_calls.append(line)

    return (
        OpenCodeExecutionStrategy(
            label_scope="test",
            registry=registry,
            subagent_activity_sink=_sink,
        ),
        sink_calls,
        registry,
    )


def test_json_child_progress_with_child_id_refreshes_sink_and_registry() -> None:
    registry = _make_registry()
    strategy, sink_calls, _registry = _make_strategy_with_sink(registry=registry)
    strategy.observe_line('{"type":"child_started","child_id":"child-A","pid":1234}')
    sink_calls.clear()
    strategy.observe_line('{"type":"child_progress","child_id":"child-A","phase":"phase-1"}')
    assert sink_calls == ['{"type":"child_progress","child_id":"child-A","phase":"phase-1"}'], (
        f"sink MUST be invoked for child_progress with child_id; got {sink_calls!r}"
    )
    snap = registry.snapshot("agent:test:")
    assert snap.active_count == 1, (
        f"registry MUST have 1 active child after child_progress;"
        f" got active_count={snap.active_count}"
    )
    assert snap.has_fresh_progress, (
        "registry MUST report has_fresh_progress=True after child_progress"
    )


def test_json_child_progress_without_child_id_does_not_refresh_sink() -> None:
    registry = _make_registry()
    strategy, sink_calls, _registry = _make_strategy_with_sink(registry=registry)
    strategy.observe_line('{"type":"child_progress","phase":"phase-1"}')
    assert sink_calls == [], (
        f"sink MUST NOT be invoked for child_progress without child_id; got {sink_calls!r}"
    )
    snap = registry.snapshot("agent:test:")
    assert snap.active_count == 0, (
        f"registry MUST also be empty (parity OK); got active_count={snap.active_count}"
    )


def test_plain_text_subagent_marker_refreshes_sink_only() -> None:
    registry = _make_registry()
    strategy, sink_calls, _registry = _make_strategy_with_sink(registry=registry)
    strategy.observe_line("[subagent] progress reading source.py")
    assert sink_calls == ["[subagent] progress reading source.py"], (
        f"plain-text marker MUST refresh the sink; got {sink_calls!r}"
    )
    snap = registry.snapshot("agent:test:")
    assert snap.active_count == 0, (
        f"registry MUST NOT be populated by plain-text markers"
        f" (registry only ingests OpenCode NDJSON);"
        f" got active_count={snap.active_count}"
    )


def test_child_complete_does_not_refresh_sink_but_updates_registry() -> None:
    registry = _make_registry()
    strategy, sink_calls, _registry = _make_strategy_with_sink(registry=registry)
    strategy.observe_line('{"type":"child_started","child_id":"child-A","pid":1234}')
    sink_calls.clear()
    strategy.observe_line(
        '{"type":"child_complete","child_id":"child-A","terminal_state":"complete"}'
    )
    assert sink_calls == [], (
        f"sink MUST NOT be invoked for child_complete (terminal signal); got {sink_calls!r}"
    )
    snap = registry.snapshot("agent:test:")
    assert snap.terminal_count == 1, (
        f"registry MUST record terminal_count=1 after child_complete;"
        f" got terminal_count={snap.terminal_count}"
    )
    assert snap.active_count == 0, (
        f"registry MUST show no active children after terminal event;"
        f" got active_count={snap.active_count}"
    )


def test_parse_opencode_child_id_returns_parsed_id() -> None:
    cases: tuple[tuple[str, str | None], ...] = (
        (
            '{"type":"child_progress","child_id":"abc","phase":"p"}',
            "abc",
        ),
        (
            '{"type":"child_progress","id":"xyz","phase":"p"}',
            "xyz",
        ),
        (
            '{"type":"child_progress","childId":"camel","phase":"p"}',
            "camel",
        ),
        (
            '{"type":"child_progress","subagent_id":"snake","phase":"p"}',
            "snake",
        ),
        (
            '{"type":"child_progress","subagentId":"mixed","phase":"p"}',
            "mixed",
        ),
        (
            '{"type":"child_progress","child_id":7,"phase":"p"}',
            "7",
        ),
        (
            '{"type":"child_progress","phase":"p"}',
            None,
        ),
        ("not json", None),
        ("", None),
    )
    for line, expected in cases:
        result = parse_opencode_child_id(line)
        assert result == expected, f"line {line!r}: expected {expected!r}, got {result!r}"


def test_camel_case_child_id_routes_progress_to_sink_and_registry() -> None:
    registry = _make_registry()
    strategy, sink_calls, _registry = _make_strategy_with_sink(registry=registry)

    strategy.observe_line('{"type":"child_started","childId":"child-A","pid":1234}')
    strategy.observe_line('{"type":"child_progress","childId":"child-A","phase":"working"}')

    assert sink_calls == ['{"type":"child_progress","childId":"child-A","phase":"working"}']
    snapshot = registry.snapshot("agent:test:")
    assert snapshot.active_count == 1
    assert snapshot.has_fresh_progress


def test_invalid_lifecycle_pids_are_not_published_as_subagent_pids() -> None:
    registry = _make_registry()
    strategy, _sink_calls, _registry = _make_strategy_with_sink(registry=registry)

    for child_id, pid in (
        ("boolean", "true"),
        ("fraction", "12.5"),
        ("zero", "0"),
        ("negative", "-1"),
    ):
        strategy.observe_line(f'{{"type":"child_started","child_id":"{child_id}","pid":{pid}}}')

    assert registry.active_pids("agent:test:") == set()


def test_parity_sink_count_matches_registry_records_for_valid_lines() -> None:
    registry = _make_registry()
    strategy, sink_calls, _registry = _make_strategy_with_sink(registry=registry)
    lines: tuple[str, ...] = (
        '{"type":"child_started","child_id":"child-A","pid":1}',
        '{"type":"child_progress","child_id":"child-A","phase":"p1"}',
        '{"type":"child_heartbeat","child_id":"child-A"}',
        '{"type":"child_started","child_id":"child-B","pid":2}',
        '{"type":"child_progress","child_id":"child-B","phase":"p2"}',
        '{"type":"child_progress","phase":"orphan"}',
    )
    for line in lines:
        strategy.observe_line(line)
    assert len(sink_calls) == 3, (
        f"sink MUST be invoked 3 times (2 progress + 1 heartbeat"
        f" with child_id); got {len(sink_calls)} invocations"
    )
    snap = registry.snapshot("agent:test:")
    assert snap.active_count == 2, (
        f"registry MUST have 2 active children (child-A, child-B);"
        f" got active_count={snap.active_count}"
    )
