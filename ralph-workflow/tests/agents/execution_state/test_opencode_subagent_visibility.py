"""OpenCode's native subagent (``task``) tool MUST be visible to Ralph.

Every line below is the REAL OpenCode 1.17.15 wire format, captured from a live
``opencode run --format json`` subagent smoke run (see
``ralph smoke-interactive-opencode --subagents``). OpenCode emits ONE terminal
event per tool call, with the call input and the result both embedded in
``part.state``:

    {"type":"tool_use","part":{"type":"tool","tool":"task","callID":"call_..",
     "state":{"status":"completed","input":{...},"output":"..."}}}

Two blind spots this pins:

1. ``classify_activity_line`` fed the idle watchdog. It matched only the event
   names ``child_progress`` / ``progress`` / ``tool_call`` / ``heartbeat``,
   none of which OpenCode emits, so EVERY OpenCode line -- including a native
   ``task`` subagent dispatch -- classified as a plain OUTPUT_LINE. The
   watchdog's ``subagent_output`` channel was never fed for OpenCode, and
   ``_tool_activity_seen`` reported "no tool activity was observed" on a run
   that had just called six tools.

2. The parser turned a completed tool event into a ``tool_result`` ONLY, never
   emitting the ``tool_use`` dispatch. ``_subagent_smoke_evidence`` counts
   dispatches by ``type == "tool_use"``, so a real subagent dispatch was
   reported as "subagent dispatch was not observed".

A running subagent tool call is genuine child-scope progress, so it maps to
CHILD_PROGRESS (which reaches the watchdog's subagent channel); terminal task
results map to CHILD_TERMINAL_ACK. Non-subagent
tools map to TOOL_USE. Bare provider frames still classify as neither -- the
strict-classifier contract in ``_helpers`` is preserved.
"""

from __future__ import annotations

import json

from ralph.agents.agent_activity_kind import AgentActivityKind
from ralph.agents.execution_state import AgentExecutionState, strategy_for_command
from ralph.agents.execution_state._helpers import parse_opencode_child_id
from ralph.agents.execution_state.opencode_execution_strategy import OpenCodeExecutionStrategy
from ralph.agents.invoke._session import extract_transport_session_id
from ralph.agents.parsers import get_parser
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle


def _tool_event(
    tool: str,
    *,
    status: str = "completed",
    call_id: str = "call_1",
    call_id_key: str = "callID",
) -> str:
    """Build a real-shaped OpenCode tool event with either supported call-ID spelling."""
    state: dict[str, object] = {"status": status, "input": {"description": "d"}}
    if status == "completed":
        state["output"] = "done"
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1784063160358,
            "sessionID": "ses_09d8d01dbffetjfSp7cD3mCTkB",
            "part": {
                "type": "tool",
                "tool": tool,
                call_id_key: call_id,
                "state": state,
            },
        }
    )


def _strategy():
    return strategy_for_command("opencode", AgentTransport.OPENCODE)


def test_running_task_tool_classifies_as_child_progress() -> None:
    """A running OpenCode task MUST reach the watchdog's subagent channel.

    ``task`` is OpenCode's subagent dispatch. Classifying its running frame as
    a plain OUTPUT_LINE left the watchdog's ``subagent_output`` channel empty
    while the child was working.
    """
    signal = _strategy().classify_activity_line(_tool_event("task", status="running"))
    assert signal is not None, "a task (subagent) dispatch must produce a signal"
    assert signal.kind == AgentActivityKind.CHILD_PROGRESS, (
        f"OpenCode 'task' is a native subagent dispatch and MUST classify as"
        f" CHILD_PROGRESS so the watchdog's subagent channel is fed; got {signal.kind}"
    )


def test_native_task_identity_does_not_misattribute_ordinary_tool_call() -> None:
    """S-3: only native subagent tools may supply a child identity.

    Every OpenCode tool has a ``part.callID``. Treating an ordinary MCP tool's
    call ID as a child ID would let future generic child-signal handling refresh
    child evidence for parent work, masking an idle parent as a live subagent.
    """
    assert parse_opencode_child_id(_tool_event("ralph_read_file", call_id="call_parent")) is None
    assert parse_opencode_child_id(_tool_event("task", call_id="call_child")) == "call_child"


def test_native_task_call_id_spelling_regression_keeps_parser_sink_and_registry_in_sync() -> None:
    """S-2/S-3: both native OpenCode call-ID spellings have one child lifecycle.

    The parser accepts ``callID`` and ``callId``.  The watchdog path must accept
    exactly the same wire contract or a valid task dispatch disappears from its
    activity sink and child registry while remaining visible in the transcript.
    """
    for call_id_key in ("callID", "callId"):
        line = _tool_event("task", status="running", call_id="call_child", call_id_key=call_id_key)
        sink_calls: list[str] = []
        clock = FakeClock()
        registry = ChildLivenessRegistry(
            progress_ttl=30.0,
            heartbeat_ttl=30.0,
            stale_label_ttl=30.0,
            exit_reconcile=30.0,
            now=clock.monotonic,
        )
        strategy = OpenCodeExecutionStrategy(
            label_scope="parent",
            registry=registry,
            subagent_activity_sink=sink_calls.append,
        )

        parsed = list(get_parser("opencode").parse(iter([line])))
        strategy.observe_line(line)

        assert [entry.type for entry in parsed] == ["tool_use"]
        assert parse_opencode_child_id(line) == "call_child"
        assert sink_calls == [line]
        assert registry.snapshot("agent:parent:").active_count == 1


def test_native_task_terminal_result_regression_releases_watchdog_child_evidence() -> None:
    """S-2: a completed task closes its child instead of refreshing progress.

    The terminal envelope is a result, not evidence that the child remains
    active. Refreshing the subagent channel here can mask a quiet parent after
    the child has completed.
    """
    sink_calls: list[str] = []
    clock = FakeClock()
    registry = ChildLivenessRegistry(
        progress_ttl=30.0,
        heartbeat_ttl=30.0,
        stale_label_ttl=30.0,
        exit_reconcile=30.0,
        now=clock.monotonic,
    )
    strategy = OpenCodeExecutionStrategy(
        label_scope="parent",
        registry=registry,
        subagent_activity_sink=sink_calls.append,
    )

    strategy.observe_line(_tool_event("task", status="running", call_id="call_real_task"))
    strategy.observe_line(_tool_event("task", status="completed", call_id="call_real_task"))

    assert sink_calls == [_tool_event("task", status="running", call_id="call_real_task")]
    assert strategy.classify_quiet(_FakeHandle(), FakeLivenessProbe(active=False)) == (
        AgentExecutionState.ACTIVE
    )


def test_native_task_regression_prefers_call_id_over_unrelated_envelope_id() -> None:
    """S-3: event-envelope IDs must not hide OpenCode's native task identity."""
    clock = FakeClock()
    registry = ChildLivenessRegistry(
        progress_ttl=30.0,
        heartbeat_ttl=30.0,
        stale_label_ttl=30.0,
        exit_reconcile=30.0,
        now=clock.monotonic,
    )
    line = json.dumps(
        {
            "type": "tool_use",
            "id": "event-envelope-id",
            "part": {
                "type": "tool",
                "tool": "task",
                "callID": "call-native-child",
                "state": {"status": "running", "input": {"description": "d"}},
            },
        }
    )
    strategy = OpenCodeExecutionStrategy(label_scope="parent", registry=registry)

    strategy.observe_line(line)

    assert parse_opencode_child_id(line) == "call-native-child"
    assert registry.snapshot("agent:parent:").active_count == 1


def test_task_tool_observe_line_records_native_task_lifecycle() -> None:
    """S-3: a native task callID must become one scoped child lifecycle.

    OpenCode does not emit separate ``child_*`` frames for the native task
    tool. The task's ``callID`` is therefore the only identity Ralph can use
    to track dispatch, work, and completion while the subagent executes.
    """
    clock = FakeClock()
    registry = ChildLivenessRegistry(
        progress_ttl=30.0,
        heartbeat_ttl=30.0,
        stale_label_ttl=30.0,
        exit_reconcile=30.0,
        now=clock.monotonic,
    )
    strategy = OpenCodeExecutionStrategy(label_scope="parent", registry=registry)

    strategy.observe_line(_tool_event("task", call_id="call_native_task"))

    snapshot = registry.snapshot("agent:parent:")
    assert snapshot.active_count == 0
    assert snapshot.terminal_count == 1


def test_native_pending_task_regression_defers_idle_watchdog_until_child_ceiling() -> None:
    """S-2: the live OpenCode task dispatch reports pending before it runs.

    The smoke transcript shows a task dispatch, then legitimate quiet work longer
    than the parent idle deadline. Treating the provider's non-terminal
    ``pending`` state as neither registered nor live makes the watchdog kill that
    healthy child before its terminal event arrives.
    """
    clock = FakeClock()
    registry = ChildLivenessRegistry(
        progress_ttl=30.0,
        heartbeat_ttl=30.0,
        stale_label_ttl=30.0,
        exit_reconcile=30.0,
        now=clock.monotonic,
    )
    strategy = OpenCodeExecutionStrategy(label_scope="parent", registry=registry)

    strategy.observe_line(_tool_event("task", status="pending", call_id="call_pending_task"))
    clock.advance(29.0)

    assert strategy.classify_quiet(_FakeHandle(), FakeLivenessProbe(active=False)) == (
        AgentExecutionState.WAITING_ON_CHILD
    )


def test_native_running_task_regression_defers_idle_watchdog_until_child_ceiling() -> None:
    """S-3: OpenCode's native task has no heartbeat frames while it runs.

    A live smoke run showed the task's explicit ``running`` dispatch followed by
    over 30 seconds without parent stdout. Treating that task as stale at the
    ordinary output deadline killed a healthy run and forced a session retry.
    A fresh native running state is authoritative child-lifecycle evidence; it
    must defer through the child-wait path, whose absolute ceiling remains enforced.
    """
    clock = FakeClock()
    registry = ChildLivenessRegistry(
        progress_ttl=30.0,
        heartbeat_ttl=30.0,
        stale_label_ttl=30.0,
        exit_reconcile=30.0,
        now=clock.monotonic,
    )
    strategy = OpenCodeExecutionStrategy(label_scope="parent", registry=registry)

    strategy.observe_line(_tool_event("task", status="running", call_id="call_live_task"))
    clock.advance(29.0)

    assert strategy.classify_quiet(_FakeHandle(), FakeLivenessProbe(active=False)) == (
        AgentExecutionState.WAITING_ON_CHILD
    )


def test_ordinary_tool_classifies_as_tool_use() -> None:
    """A non-subagent tool call MUST classify as TOOL_USE, not a bare output line.

    ``_tool_activity_seen`` looks for TOOL_USE. Reporting "no tool activity was
    observed" for a run that called six tools is a false negative.
    """
    signal = _strategy().classify_activity_line(_tool_event("ralph_mcp__ralph__write_file"))
    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE, (
        f"an OpenCode tool call MUST classify as TOOL_USE; got {signal.kind}"
    )


def test_bare_provider_frame_is_not_a_child_signal() -> None:
    """The strict-classifier contract holds: a bare frame is not child activity.

    A parent-level ``{"type":"heartbeat"}`` frame must NOT be read as proof that
    a subagent is alive -- that was the false-positive deferral documented in
    ``_helpers``. Only a real tool/child event counts.
    """
    signal = _strategy().classify_activity_line(json.dumps({"type": "heartbeat"}))
    child_kinds = {AgentActivityKind.CHILD_PROGRESS, AgentActivityKind.CHILD_HEARTBEAT}
    assert signal is None or signal.kind not in child_kinds, (
        f"a bare provider frame must not count as child activity; got {signal}"
    )


def test_parser_emits_dispatch_and_result_for_completed_tool() -> None:
    """A completed tool event MUST surface BOTH the dispatch and the result.

    OpenCode collapses call+result into one terminal event. Emitting only the
    ``tool_result`` erased the dispatch, so ``_subagent_smoke_evidence`` counted
    zero dispatches and the smoke reported "subagent dispatch was not observed"
    for a subagent that had actually run.
    """
    parser = get_parser("opencode")
    parsed = list(parser.parse(iter([_tool_event("task")])))
    types = [p.type for p in parsed]

    assert "tool_use" in types, (
        f"the dispatch MUST be emitted so subagent dispatch is countable; got {types}"
    )
    assert "tool_result" in types, f"the result MUST still be emitted; got {types}"
    assert types.index("tool_use") < types.index("tool_result"), (
        f"dispatch MUST precede result so ordered lifecycle checks hold; got {types}"
    )
    dispatch = parsed[types.index("tool_use")]
    assert dispatch.content == "task", (
        f"the dispatch MUST name the tool so subagent tools are identifiable;"
        f" got {dispatch.content!r}"
    )


def test_opencode_session_id_is_extracted() -> None:
    """OpenCode's ``sessionID`` MUST be recognised as a transport session ID.

    OpenCode stamps ``sessionID`` (capital I, capital D) on EVERY event rather
    than emitting a dedicated ``session``/``session_start`` frame. The extractor
    only looked for ``session_id`` / ``sessionId`` / ``id``, and only on a
    whitelist of event types OpenCode never emits -- so the session was never
    captured and the smoke reported "session ID was not observed" on a run whose
    every line carried one.
    """
    session_id = extract_transport_session_id([_tool_event("task")])
    assert session_id == "ses_09d8d01dbffetjfSp7cD3mCTkB", (
        f"OpenCode's sessionID must be extracted; got {session_id!r}"
    )
