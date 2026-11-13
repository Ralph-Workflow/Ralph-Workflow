from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.parsers.agy import AgyParser
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)
from ralph.agents.parsers.codex import CodexParser
from ralph.agents.parsers.content_channel import ContentChannel
from ralph.agents.parsers.cursor import CursorParser
from ralph.agents.parsers.gemini import GeminiParser
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.parsers.kimi import KimiParser
from ralph.agents.parsers.nanocoder import NanocoderParser
from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.agents.parsers.pi import PiParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestAgentOutputLineChannelModel:
    def test_default_channel_is_provider(self) -> None:
        assert AgentOutputLine(type="text").channel is ContentChannel.PROVIDER

    def test_channel_follows_emitted_tool_call_type(self) -> None:
        assert AgentOutputLine(type="tool_use").channel is ContentChannel.TOOL_CALL

    def test_channel_follows_emitted_tool_result_type(self) -> None:
        assert AgentOutputLine(type="tool_result").channel is ContentChannel.TOOL_RESULT

    def test_channel_follows_emitted_thinking_type(self) -> None:
        assert AgentOutputLine(type="thinking").channel is ContentChannel.REASONING

    def test_channel_follows_emitted_subagent_type(self) -> None:
        assert AgentOutputLine(type="subagent").channel is ContentChannel.SUBAGENT

    def test_channel_follows_emitted_user_type(self) -> None:
        assert AgentOutputLine(type="user").channel is ContentChannel.USER

    def test_explicit_channel_is_preserved(self) -> None:
        line = AgentOutputLine(type="error", channel=ContentChannel.TOOL_RESULT)
        assert line.channel is ContentChannel.TOOL_RESULT


class TestGenericParserChannels:
    def test_plain_tool_line_is_tool_call(self) -> None:
        results = list(GenericParser().parse(_lines("[plain] tool: bash")))
        assert [line.channel for line in results] == [ContentChannel.TOOL_CALL]

    def test_thinking_event_is_reasoning(self) -> None:
        results = list(GenericParser().parse(_lines(_line({"type": "message", "thought": "why"}))))
        assert [line.channel for line in results] == [ContentChannel.REASONING]

    @pytest.mark.parametrize(
        "raw",
        [
            _line({"type": "error", "error": "boom"}),
            "just a status update",
        ],
    )
    def test_error_and_unparsed_lines_are_provider(self, raw: str) -> None:
        results = list(GenericParser().parse(_lines(raw)))
        assert [line.channel for line in results] == [ContentChannel.PROVIDER]


class TestClaudeParserChannels:
    def test_tool_use_block_is_tool_call(self) -> None:
        raw = _line(
            {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}],
                },
            }
        )
        results = list(ClaudeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_errored_tool_result_block_is_tool_result_not_provider(self) -> None:
        raw = _line(
            {
                "type": "user",
                "message": {
                    "id": "m2",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "is_error": True,
                            "content": "RESOURCE_EXHAUSTED (code 429)",
                        }
                    ],
                },
            }
        )
        results = list(ClaudeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.TOOL_RESULT)
        ]

    def test_thinking_block_is_reasoning(self) -> None:
        raw = _line(
            {
                "type": "assistant",
                "message": {
                    "id": "m3",
                    "content": [{"type": "thinking", "thinking": "planning"}],
                },
            }
        )
        results = list(ClaudeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]

    def test_error_event_is_provider(self) -> None:
        raw = _line({"type": "error", "error": {"message": "quota exhausted"}})
        results = list(ClaudeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]


class TestCodexParserChannels:
    def test_reasoning_item_is_reasoning(self) -> None:
        raw = _line({"type": "item.completed", "item": {"type": "reasoning", "text": "hmm"}})
        results = list(CodexParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]

    def test_completed_tool_call_item_is_tool_result(self) -> None:
        raw = _line(
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "tool": "read_multiple_files",
                    "result": {"content": [{"type": "text", "text": "ok"}]},
                },
            }
        )
        results = list(CodexParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_result", ContentChannel.TOOL_RESULT)
        ]

    def test_tool_use_item_is_tool_call(self) -> None:
        raw = _line(
            {
                "type": "item.started",
                "item": {
                    "type": "command_execution",
                    "command": "echo hi",
                    "id": "c1",
                },
            }
        )
        results = list(CodexParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_error_event_is_provider(self) -> None:
        raw = _line({"type": "error", "message": "RESOURCE_EXHAUSTED (code 429)"})
        results = list(CodexParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]


class TestOpenCodeParserChannels:
    def test_running_tool_use_is_tool_call(self) -> None:
        raw = _line(
            {
                "type": "tool_use",
                "part": {"tool": "bash", "state": {"status": "running"}},
            }
        )
        results = list(OpenCodeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_completed_tool_state_is_tool_call_then_tool_result(self) -> None:
        raw = _line(
            {
                "type": "tool_use",
                "part": {"tool": "bash", "state": {"status": "completed", "output": "ok"}},
            }
        )
        results = list(OpenCodeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL),
            ("tool_result", ContentChannel.TOOL_RESULT),
        ]

    def test_errored_tool_state_is_tool_result_not_provider(self) -> None:
        raw = _line(
            {
                "type": "tool_use",
                "part": {"tool": "bash", "state": {"status": "error", "error": "boom"}},
            }
        )
        results = list(OpenCodeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL),
            ("error", ContentChannel.TOOL_RESULT),
        ]

    def test_error_event_is_provider(self) -> None:
        raw = _line({"type": "error", "error": "quota exhausted"})
        results = list(OpenCodeParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]


class TestGeminiParserChannels:
    def test_tool_call_event_is_tool_call(self) -> None:
        raw = _line({"type": "tool_call", "name": "lookup", "args": {"q": "x"}})
        results = list(GeminiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_function_call_event_is_tool_result(self) -> None:
        raw = _line({"type": "function_call", "response": "done"})
        results = list(GeminiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_result", ContentChannel.TOOL_RESULT)
        ]

    def test_thought_part_is_reasoning(self) -> None:
        raw = _line({"type": "message", "parts": [{"text": "why", "thought": True}]})
        results = list(GeminiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]

    def test_error_event_is_provider(self) -> None:
        raw = _line({"type": "error", "message": "quota exhausted"})
        results = list(GeminiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]


class TestCursorParserChannels:
    def test_tool_call_event_is_tool_call(self) -> None:
        raw = _line({"type": "tool_call", "toolName": "bash", "args": {}})
        results = list(CursorParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_completed_tool_call_event_is_tool_result(self) -> None:
        raw = _line(
            {
                "type": "tool_call",
                "subtype": "completed",
                "toolName": "bash",
                "result": {"content": "ok"},
            }
        )
        results = list(CursorParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_result", ContentChannel.TOOL_RESULT)
        ]

    def test_errored_tool_result_is_tool_result_not_provider(self) -> None:
        raw = _line({"type": "tool_result", "error": "RESOURCE_EXHAUSTED (code 429)"})
        results = list(CursorParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.TOOL_RESULT)
        ]

    def test_thinking_event_is_reasoning(self) -> None:
        parser = CursorParser()
        results = list(parser.parse(_lines(_line({"type": "thinking", "text": "hmm"}))))
        results.extend(parser.flush_accumulators())
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]


class TestKimiParserChannels:
    def test_assistant_tool_call_is_tool_call(self) -> None:
        raw = _line(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "Read", "arguments": "{}"}}],
            }
        )
        results = list(KimiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_tool_result_is_tool_result(self) -> None:
        raw = _line({"role": "tool", "tool_call_id": "c1", "content": "ok"})
        results = list(KimiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_result", ContentChannel.TOOL_RESULT)
        ]

    def test_errored_tool_payload_is_tool_result_not_provider(self) -> None:
        raw = _line(
            {
                "role": "tool",
                "tool_call_id": "c1",
                "is_error": True,
                "content": "RESOURCE_EXHAUSTED (code 429)",
            }
        )
        results = list(KimiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.TOOL_RESULT)
        ]

    def test_meta_event_is_provider(self) -> None:
        raw = _line({"role": "meta", "type": "system.version", "version": "1.0"})
        results = list(KimiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("lifecycle", ContentChannel.PROVIDER)
        ]


class TestPiParserChannels:
    def test_tool_execution_start_is_tool_call(self) -> None:
        raw = _line({"type": "tool_execution_start", "toolName": "bash", "args": {}})
        results = list(PiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_errored_tool_execution_end_is_tool_result_not_provider(self) -> None:
        raw = _line(
            {
                "type": "tool_execution_end",
                "toolName": "bash",
                "isError": True,
                "result": "RESOURCE_EXHAUSTED (code 429)",
            }
        )
        results = list(PiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.TOOL_RESULT)
        ]

    def test_tool_result_block_is_tool_result(self) -> None:
        raw = _line(
            {
                "type": "message_end",
                "message": {
                    "content": [
                        {
                            "type": "toolResult",
                            "result": {"content": [{"type": "text", "text": "ok"}]},
                        }
                    ]
                },
            }
        )
        results = list(PiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_result", ContentChannel.TOOL_RESULT)
        ]

    def test_thinking_end_is_reasoning(self) -> None:
        raw = _line(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "thinking_end",
                    "content": "plan",
                    "contentIndex": 0,
                },
            }
        )
        results = list(PiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]

    def test_extension_error_is_provider(self) -> None:
        raw = _line({"type": "extension_error", "error": "quota exhausted"})
        results = list(PiParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]


class TestAgyParserChannels:
    def test_tool_step_done_is_tool_call_then_tool_result(self) -> None:
        raw = _line(
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "step_index": 2,
                    "state": "DONE",
                    "tool_info": {"name": "Read", "parameters": {"file_path": "/x"}},
                    "duration_seconds": 1.0,
                },
            }
        )
        results = list(AgyParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL),
            ("tool_result", ContentChannel.TOOL_RESULT),
        ]

    def test_subagent_step_frames_are_subagent(self) -> None:
        raw = _line(
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "subagent",
                    "step_index": 1,
                    "state": "DONE",
                    "subagent_info": {"subagents": [{"role": "worker", "type_name": "explore"}]},
                },
            }
        )
        results = list(AgyParser().parse(_lines(raw)))
        assert [line.channel for line in results] == [
            ContentChannel.SUBAGENT,
            ContentChannel.SUBAGENT,
        ]

    def test_error_event_is_provider(self) -> None:
        raw = _line({"event": "error", "message": "RESOURCE_EXHAUSTED (code 429)"})
        results = list(AgyParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]


class TestClaudeInteractiveParserChannels:
    def test_tool_use_event_is_tool_call(self) -> None:
        raw = _line(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]
                },
            }
        )
        results = list(ClaudeInteractiveParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_errored_tool_result_is_tool_result_not_provider(self) -> None:
        parser = ClaudeInteractiveParser()
        tool_use = _line(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {}}]
                },
            }
        )
        tool_result = _line(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "is_error": True,
                            "content": "RESOURCE_EXHAUSTED (code 429)",
                        }
                    ]
                },
            }
        )
        results = list(parser.parse(_lines(tool_use, tool_result)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL),
            ("error", ContentChannel.TOOL_RESULT),
        ]

    def test_provider_failure_text_is_provider(self) -> None:
        results = list(ClaudeInteractiveParser().parse(_lines("500 internal server error: boom\n")))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]

    def test_thinking_event_is_reasoning(self) -> None:
        parser = ClaudeInteractiveParser()
        raw = _line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "thinking": "plan"}]},
            }
        )
        results = list(parser.parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("thinking", ContentChannel.REASONING)
        ]


class TestNanocoderParserChannels:
    def test_executed_tool_line_is_tool_call(self) -> None:
        results = list(NanocoderParser().parse(_lines("⚒ Executed Read " + chr(215) + " 2")))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_plain_tool_line_is_tool_call(self) -> None:
        results = list(NanocoderParser().parse(_lines("[plain] tool: bash")))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL)
        ]

    def test_text_line_is_provider(self) -> None:
        results = list(NanocoderParser().parse(_lines("hello world this is output")))
        assert [(line.type, line.channel) for line in results] == [
            ("text", ContentChannel.PROVIDER)
        ]


class TestTranscriptParserEventKinds:
    def test_transcript_parser_kinds_cover_typed_frames(self) -> None:
        parser = ClaudeInteractiveTranscriptParser()
        events = parser.feed(
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "plan"},
                            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
                        ]
                    },
                }
            )
        )
        assert [event.kind for event in events] == ["thinking", "tool_use"]
