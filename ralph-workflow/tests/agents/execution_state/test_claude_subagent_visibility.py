"""Claude's nested tool calls -- including the native ``Task`` subagent -- must be seen.

Claude's ``--output-format=stream-json`` does NOT put the tool call at the top
level. It nests it inside an ``assistant`` message (shape captured from a real
run in ``.agent/raw/claude.log``)::

    {"type":"assistant","message":{"content":[
        {"type":"tool_use","name":"mcp__ralph__create_directory", ...}]}}

The classifier only matched a TOP-LEVEL ``{"type":"tool_use"}``, so across 331
real transcript lines it returned ZERO TOOL_USE and ZERO CHILD_* -- every line
fell through to OUTPUT_LINE.

For the idle watchdog this is the dangerous case. ``Task`` is Claude's native
subagent dispatch. If a ``Task`` call never reaches the watchdog's
``subagent_output`` channel, then while a subagent is legitimately working the
channel stays empty/stale, the agent looks silent, and the watchdog concludes
the subagent is dead. Feeding CHILD_PROGRESS is what tells the watchdog "a
subagent is doing work here" -- it is the signal that keeps a working subagent
alive.
"""

from __future__ import annotations

import json

from ralph.agents.agent_activity_kind import AgentActivityKind
from ralph.agents.execution_state import strategy_for_command
from ralph.config.enums import AgentTransport


def _assistant_tool_use(name: str) -> str:
    """Real Claude stream-json shape: tool_use nested in an assistant message."""
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_01ABC",
                        "name": name,
                        "input": {"description": "d"},
                    }
                ],
            },
        }
    )


def _strategy():
    return strategy_for_command("claude", AgentTransport.CLAUDE)


def test_nested_tool_use_classifies_as_tool_use() -> None:
    """A nested MCP tool call MUST classify as TOOL_USE, not a bare output line."""
    signal = _strategy().classify_activity_line(_assistant_tool_use("mcp__ralph__create_directory"))
    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE, (
        f"Claude nests tool_use inside message.content; it MUST still classify as"
        f" TOOL_USE. got {signal.kind}"
    )


def test_task_tool_classifies_as_child_progress() -> None:
    """Claude's native ``Task`` subagent MUST reach the watchdog's subagent channel.

    Without this the watchdog never learns a subagent exists: while the subagent
    works, ``subagent_output`` stays empty, the run looks silent, and the
    watchdog treats a live subagent as a dead one.
    """
    signal = _strategy().classify_activity_line(_assistant_tool_use("Task"))
    assert signal is not None
    assert signal.kind == AgentActivityKind.CHILD_PROGRESS, (
        f"Claude's 'Task' is a native subagent dispatch and MUST classify as"
        f" CHILD_PROGRESS so the watchdog's subagent channel is fed; got {signal.kind}"
    )


def test_plain_assistant_text_is_not_tool_activity() -> None:
    """An assistant message with no tool block MUST NOT be reported as tool activity."""
    line = json.dumps(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        }
    )
    signal = _strategy().classify_activity_line(line)
    assert signal is None or signal.kind not in {
        AgentActivityKind.TOOL_USE,
        AgentActivityKind.CHILD_PROGRESS,
    }, f"plain assistant text must not count as tool/child activity; got {signal}"
