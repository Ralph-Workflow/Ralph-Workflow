"""Pin: OpenCode watchdog activity sink and child-liveness registry parity.

The pre-fix bug: ``opencode_execution_strategy.py:observe_line``
refreshed the watchdog's subagent-activity sink for
``CHILD_PROGRESS`` / ``CHILD_HEARTBEAT`` lines even when the
parsed JSON line lacked a ``child_id`` (or ``id``) field. The
child-liveness registry, in contrast, dropped the same line
unless ``child_id`` was present. The watchdog could therefore
treat a line as fresh subagent activity while the registry-backed
liveness view stayed stale, which is a real parity risk for the
PROMPT's "print and extract subagent and what it's doing in real
time" requirement.

The fix: the OpenCode strategy now parses ``child_id`` from the
line and gates the sink on whether the parsed line carries an
explicit ``child_id`` (OpenCode JSON envelopes) OR is a plain-text
generic marker (``[subagent] progress`` etc., which carry implicit
child scoping via the marker text). The registry's gate is
unchanged. The two surfaces now consume the SAME evidence model.

This test pins:

1. An OpenCode JSON ``child_progress`` line WITH a ``child_id``
   refreshes the sink AND populates the registry (parity OK).
2. An OpenCode JSON ``child_progress`` line WITHOUT a ``child_id``
   does NOT refresh the sink AND does NOT populate the registry
   (parity OK; pre-fix the sink was refreshed but the registry
   was not).
3. A plain-text generic marker (``[subagent] progress``) refreshes
   the sink (child scoping is implicit in the marker text) AND
   does NOT populate the registry (plain-text markers carry no
   ``child_id`` and the registry only ingests OpenCode NDJSON).
4. A ``child_complete`` (terminal) line does NOT refresh the
   sink (terminal signals are not forward progress) AND DOES
   populate the registry's terminal-ack state.
5. The new ``parse_opencode_child_id`` helper returns the parsed
   ``child_id`` for valid JSON, ``None`` for invalid JSON, and
   ``None`` for valid JSON without ``child_id``.

Black-box: drive ``OpenCodeExecutionStrategy.observe_line``
directly with synthetic lines and capture sink invocations via a
constructor-injected sink. The registry is exercised by
constructing a real ``ChildLivenessRegistry`` and observing its
``snapshot`` after each line.
"""

from __future__ import annotations

from ralph.agents.execution_state._helpers import parse_opencode_child_id
from ralph.agents.execution_state.opencode_execution_strategy import (
    OpenCodeExecutionStrategy,
)
from ralph.process.child_liveness import ChildLivenessRegistry


def _make_registry() -> ChildLivenessRegistry:
    """Build a ``ChildLivenessRegistry`` with non-zero TTLs so
    ``register_child`` accepts the test lines.
    """
    return ChildLivenessRegistry(
        progress_ttl=180.0,
        heartbeat_ttl=180.0,
        stale_label_ttl=600.0,
        exit_reconcile=10.0,
    )


def _make_strategy_with_sink(
    registry: ChildLivenessRegistry | None = None,
) -> tuple[OpenCodeExecutionStrategy, list[str], ChildLivenessRegistry | None]:
    """Build a strategy with a captured sink and a fresh registry.

    The constructor-injected sink is the production-aligned seam
    the watchdog uses to refresh its per-channel evidence surface.
    """
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
    """A JSON ``child_progress`` line WITH a ``child_id`` MUST
    refresh the sink AND populate the registry (parity OK).
    """
    registry = _make_registry()
    strategy, sink_calls, _registry = _make_strategy_with_sink(registry=registry)
    # Register the child first so the registry has a record to update.
    strategy.observe_line('{"type":"child_started","child_id":"child-A","pid":1234}')
    sink_calls.clear()  # ignore the spawn sink call (it's not a progress signal)
    strategy.observe_line('{"type":"child_progress","child_id":"child-A","phase":"phase-1"}')
    assert sink_calls == ['{"type":"child_progress","child_id":"child-A","phase":"phase-1"}'], (
        f"sink MUST be invoked for child_progress with child_id; got {sink_calls!r}"
    )
    # The registry MUST also have the progress recorded for child-A.
    snap = registry.snapshot("agent:test:")
    assert snap.active_count == 1, (
        f"registry MUST have 1 active child after child_progress;"
        f" got active_count={snap.active_count}"
    )
    assert snap.has_fresh_progress, (
        "registry MUST report has_fresh_progress=True after child_progress"
    )


def test_json_child_progress_without_child_id_does_not_refresh_sink() -> None:
    """A JSON ``child_progress`` line WITHOUT a ``child_id`` MUST
    NOT refresh the sink (parity fix).

    The pre-fix bug: the sink was invoked for any line that
    classified as CHILD_PROGRESS, regardless of whether the line
    carried an explicit ``child_id``. The registry dropped the
    same line. The watchdog therefore treated un-attributable
    lines as fresh subagent activity while the registry-backed
    liveness view stayed stale.
    """
    registry = _make_registry()
    strategy, sink_calls, _registry = _make_strategy_with_sink(registry=registry)
    # The line is valid OpenCode NDJSON but lacks ``child_id``.
    # Pre-fix: the sink was invoked and the registry was not
    # (parity bug). Post-fix: NEITHER surface is updated.
    strategy.observe_line('{"type":"child_progress","phase":"phase-1"}')
    assert sink_calls == [], (
        f"sink MUST NOT be invoked for child_progress without child_id; got {sink_calls!r}"
    )
    snap = registry.snapshot("agent:test:")
    assert snap.active_count == 0, (
        f"registry MUST also be empty (parity OK); got active_count={snap.active_count}"
    )


def test_plain_text_subagent_marker_refreshes_sink_only() -> None:
    """A plain-text generic marker (``[subagent] progress``)
    classifies as CHILD_PROGRESS and refreshes the sink
    (child scoping is implicit in the marker text). The registry
    only ingests OpenCode NDJSON, so plain-text markers do NOT
    populate the registry.
    """
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
    """A ``child_complete`` (terminal) line MUST NOT refresh the
    sink (terminal signals are not forward progress) AND MUST
    populate the registry's terminal-ack state (so the corroborator
    sees the child is no longer running).
    """
    registry = _make_registry()
    strategy, sink_calls, _registry = _make_strategy_with_sink(registry=registry)
    # Register the child first.
    strategy.observe_line('{"type":"child_started","child_id":"child-A","pid":1234}')
    sink_calls.clear()
    # Send a terminal event.
    strategy.observe_line(
        '{"type":"child_complete","child_id":"child-A","terminal_state":"complete"}'
    )
    assert sink_calls == [], (
        f"sink MUST NOT be invoked for child_complete (terminal signal); got {sink_calls!r}"
    )
    # The registry MUST record the terminal-ack.
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
    """``parse_opencode_child_id`` MUST return the parsed
    ``child_id`` (or ``id``) for valid OpenCode JSON."""
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
            '{"type":"child_progress","phase":"p"}',
            None,
        ),
        ("not json", None),
        ("", None),
    )
    for line, expected in cases:
        result = parse_opencode_child_id(line)
        assert result == expected, f"line {line!r}: expected {expected!r}, got {result!r}"


def test_parity_sink_count_matches_registry_records_for_valid_lines() -> None:
    """End-to-end parity check: for a sequence of valid OpenCode
    NDJSON lines WITH child_ids, the number of sink invocations
    MUST equal the number of progress/heartbeat records the
    registry sees.
    """
    registry = _make_registry()
    strategy, sink_calls, _registry = _make_strategy_with_sink(registry=registry)
    lines: tuple[str, ...] = (
        '{"type":"child_started","child_id":"child-A","pid":1}',
        '{"type":"child_progress","child_id":"child-A","phase":"p1"}',
        '{"type":"child_heartbeat","child_id":"child-A"}',
        '{"type":"child_started","child_id":"child-B","pid":2}',
        '{"type":"child_progress","child_id":"child-B","phase":"p2"}',
        # The line below lacks child_id; parity requires BOTH
        # surfaces to drop it.
        '{"type":"child_progress","phase":"orphan"}',
    )
    for line in lines:
        strategy.observe_line(line)
    # Sink MUST have been invoked for the 2 progress + 1 heartbeat
    # lines that have a child_id (the 2 spawn lines and the orphan
    # progress line do NOT refresh the sink because the spawn
    # event is a CHILD_PROCESS kind -- not CHILD_PROGRESS /
    # CHILD_HEARTBEAT).
    assert len(sink_calls) == 3, (
        f"sink MUST be invoked 3 times (2 progress + 1 heartbeat"
        f" with child_id); got {len(sink_calls)} invocations"
    )
    # Registry MUST have 2 active children (child-A, child-B); the
    # orphan progress line MUST be dropped.
    snap = registry.snapshot("agent:test:")
    assert snap.active_count == 2, (
        f"registry MUST have 2 active children (child-A, child-B);"
        f" got active_count={snap.active_count}"
    )
