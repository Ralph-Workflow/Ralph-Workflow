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

A subagent tool call is a genuine child-scope signal, so it maps to
CHILD_PROGRESS (which reaches the watchdog's subagent channel). Non-subagent
tools map to TOOL_USE. Bare provider frames still classify as neither -- the
strict-classifier contract in ``_helpers`` is preserved.
"""

from __future__ import annotations

import json

from ralph.agents.agent_activity_kind import AgentActivityKind
from ralph.agents.execution_state import strategy_for_command
from ralph.agents.invoke._session import extract_transport_session_id
from ralph.agents.parsers import get_parser
from ralph.config.enums import AgentTransport


def _tool_event(tool: str, *, status: str = "completed", call_id: str = "call_1") -> str:
    """Build a REAL-shaped OpenCode tool event."""
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
                "callID": call_id,
                "state": state,
            },
        }
    )


def _strategy():
    return strategy_for_command("opencode", AgentTransport.OPENCODE)


def test_task_tool_classifies_as_child_progress() -> None:
    """OpenCode's native subagent tool MUST reach the watchdog's subagent channel.

    ``task`` is OpenCode's subagent dispatch. Classifying it as a plain
    OUTPUT_LINE left the watchdog's ``subagent_output`` channel empty for the
    entire run, so it had no idea a subagent existed.
    """
    signal = _strategy().classify_activity_line(_tool_event("task"))
    assert signal is not None, "a task (subagent) dispatch must produce a signal"
    assert signal.kind == AgentActivityKind.CHILD_PROGRESS, (
        f"OpenCode 'task' is a native subagent dispatch and MUST classify as"
        f" CHILD_PROGRESS so the watchdog's subagent channel is fed; got {signal.kind}"
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
