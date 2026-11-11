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
    signal = _strategy().classify_activity_line(_tool_event("task", status="running"))
    assert signal is not None, "a task (subagent) dispatch must produce a signal"
    assert signal.kind == AgentActivityKind.CHILD_PROGRESS, (
        f"OpenCode 'task' is a native subagent dispatch and MUST classify as"
        f" CHILD_PROGRESS so the watchdog's subagent channel is fed; got {signal.kind}"
    )


def test_native_task_identity_does_not_misattribute_ordinary_tool_call() -> None:
    assert parse_opencode_child_id(_tool_event("ralph_read_file", call_id="call_parent")) is None
    assert parse_opencode_child_id(_tool_event("task", call_id="call_child")) == "call_child"


def test_native_task_call_id_spelling_regression_keeps_parser_sink_and_registry_in_sync() -> None:
    for tool_name in ("task", "Task"):
        for call_id_key in ("callID", "callId"):
            line = _tool_event(
                tool_name,
                status="running",
                call_id="call_child",
                call_id_key=call_id_key,
            )
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
    signal = _strategy().classify_activity_line(_tool_event("ralph_mcp__ralph__write_file"))
    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE, (
        f"an OpenCode tool call MUST classify as TOOL_USE; got {signal.kind}"
    )


def test_bare_provider_frame_is_not_a_child_signal() -> None:
    signal = _strategy().classify_activity_line(json.dumps({"type": "heartbeat"}))
    child_kinds = {AgentActivityKind.CHILD_PROGRESS, AgentActivityKind.CHILD_HEARTBEAT}
    assert signal is None or signal.kind not in child_kinds, (
        f"a bare provider frame must not count as child activity; got {signal}"
    )


def test_parser_emits_dispatch_and_result_for_completed_tool() -> None:
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
    session_id = extract_transport_session_id([_tool_event("task")])
    assert session_id == "ses_09d8d01dbffetjfSp7cD3mCTkB", (
        f"OpenCode's sessionID must be extracted; got {session_id!r}"
    )
