from __future__ import annotations

import json

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.invoke._tool_call_extraction import extract_tool_call_from_activity_signal
from ralph.config.enums import AgentTransport


class TestToolCallExtraction:
    def test_agy_step_update_tool_info_unwraps_name_and_parameters(self) -> None:
        line = json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "00000000-0000-0000-0000-000000000002",
                    "step_index": 3,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": "write_to_file",
                    "tool_info": {
                        "name": "write_to_file",
                        "parameters": {"TargetFile": "/workspace/hello.txt"},
                    },
                },
            }
        )

        result = extract_tool_call_from_activity_signal(line)

        assert result is not None
        tool_name, tool_args = result
        assert tool_name == "write_to_file"
        assert tool_args == {"TargetFile": "/workspace/hello.txt"}

    def test_agy_step_update_tool_name_without_tool_info_yields_empty_args(self) -> None:
        line = json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 3,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": "write_to_file",
                },
            }
        )

        result = extract_tool_call_from_activity_signal(line)

        assert result is not None
        tool_name, tool_args = result
        assert tool_name == "write_to_file"
        assert tool_args == {}

    def test_agy_non_tool_step_update_returns_none(self) -> None:
        line = json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 2,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "text_delta": "hello",
                },
            }
        )

        assert extract_tool_call_from_activity_signal(line) is None

    def test_claude_content_block_start_tool_use_unwraps_name_and_input(self) -> None:
        line = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"path": "ralph/display/record_writer.py"},
                },
            }
        )

        result = extract_tool_call_from_activity_signal(line)

        assert result is not None
        tool_name, tool_args = result
        assert tool_name == "Read"
        assert tool_args == {"path": "ralph/display/record_writer.py"}

    def test_claude_content_block_start_text_block_returns_none(self) -> None:
        line = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {"type": "text", "text": "Claude checks the display."},
            }
        )

        assert extract_tool_call_from_activity_signal(line) is None

    def test_pi_toolcall_end_unwraps_toolcall_name_and_input(self) -> None:
        line = json.dumps(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "toolcall_end",
                    "contentIndex": 1,
                    "toolCall": {
                        "id": "call-1",
                        "name": "Read",
                        "input": {"path": "ralph/display/record_writer.py"},
                    },
                },
            }
        )

        result = extract_tool_call_from_activity_signal(line)

        assert result is not None
        tool_name, tool_args = result
        assert tool_name == "Read"
        assert tool_args == {"path": "ralph/display/record_writer.py"}

    def test_non_tool_json_line_returns_none(self) -> None:
        line = json.dumps({"type": "assistant", "message": {"content": []}})

        assert extract_tool_call_from_activity_signal(line) is None

    def test_pi_strategy_classifies_toolcall_end_as_tool_use(self) -> None:
        line = json.dumps(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "toolcall_end",
                    "contentIndex": 1,
                    "toolCall": {
                        "id": "call-1",
                        "name": "Read",
                        "input": {"path": "ralph/display/record_writer.py"},
                    },
                },
            }
        )

        signal = strategy_for_transport(AgentTransport.PI).classify_activity_line(line)

        assert signal is not None
        assert signal.kind == AgentActivityKind.TOOL_USE
        assert signal.raw == line
