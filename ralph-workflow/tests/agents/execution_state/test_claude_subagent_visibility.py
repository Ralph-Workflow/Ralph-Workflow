from __future__ import annotations

import json

from ralph.agents.agent_activity_kind import AgentActivityKind
from ralph.agents.execution_state import strategy_for_command
from ralph.config.enums import AgentTransport


def _assistant_tool_use(name: str) -> str:
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
    signal = _strategy().classify_activity_line(_assistant_tool_use("mcp__ralph__create_directory"))
    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE, (
        f"Claude nests tool_use inside message.content; it MUST still classify as"
        f" TOOL_USE. got {signal.kind}"
    )


def test_task_tool_classifies_as_child_progress() -> None:
    signal = _strategy().classify_activity_line(_assistant_tool_use("Task"))
    assert signal is not None
    assert signal.kind == AgentActivityKind.CHILD_PROGRESS, (
        f"Claude's 'Task' is a native subagent dispatch and MUST classify as"
        f" CHILD_PROGRESS so the watchdog's subagent channel is fed; got {signal.kind}"
    )


def test_plain_assistant_text_is_not_tool_activity() -> None:
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
