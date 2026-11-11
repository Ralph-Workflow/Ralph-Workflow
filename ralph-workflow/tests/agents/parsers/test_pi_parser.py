from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers import NdjsonParserBase
from ralph.agents.parsers.pi import PiParser

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.parsers.agent_output_line import AgentOutputLine


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestPiParserSubclassRelationship:
    def test_subclass_of_ndjson_base(self) -> None:
        assert issubclass(PiParser, NdjsonParserBase)

    def test_has_parse_method(self) -> None:
        parser = PiParser()
        assert callable(parser.parse)


class TestPiParserSharedNdjsonBehaviors:
    def test_data_prefix_stripped(self) -> None:
        parser = PiParser()
        results = list(
            parser.parse(
                _lines(
                    'data: {"type": "tool_execution_start", '
                    '"toolCallId": "c1", "toolName": "bash"}',
                ),
            )
        )
        assert len(results) == 1
        assert results[0].type == "tool_use"
        assert results[0].content == "bash"

    def test_done_sentinel_yields_stop(self) -> None:
        parser = PiParser()
        results = list(parser.parse(_lines("[DONE]")))
        assert len(results) == 1
        assert results[0].type == "stop"

    def test_non_json_line_yields_raw(self) -> None:
        parser = PiParser()
        results = list(parser.parse(_lines("not json at all")))
        assert len(results) == 1
        assert results[0].type == "raw"
        assert results[0].content == "not json at all"

    def test_non_dict_json_yields_raw(self) -> None:
        parser = PiParser()
        results = list(parser.parse(_lines("[1, 2, 3]")))
        assert len(results) == 1
        assert results[0].type == "raw"

        results = list(parser.parse(_lines('"just a string"')))
        assert len(results) == 1
        assert results[0].type == "raw"

    def test_message_start_routes_through_dispatch_and_is_silent(self) -> None:
        parser = PiParser()
        line = _line({"type": "message_start", "message": {"role": "assistant"}})
        results = list(parser.parse(_lines(line)))
        assert results == []

    def test_message_start_reaches_dispatch(self) -> None:
        seen_event_types: list[str] = []

        class _RecordingPiParser(PiParser):
            def _dispatch_json_object(
                self,
                obj: dict[str, object],
                raw: str,
                source_timestamp: str | None = None,
            ) -> Iterator[AgentOutputLine]:
                seen_event_types.append(str(obj.get("type", "")))
                yield from super()._dispatch_json_object(obj, raw, source_timestamp)

        parser = _RecordingPiParser()
        line = _line({"type": "message_start", "message": {"role": "assistant"}})
        results = list(parser.parse(_lines(line)))

        assert results == []
        assert "message_start" in seen_event_types

    def test_error_field_produces_error_line(self) -> None:
        parser = PiParser()
        line = _line({"error": {"message": "boom"}})
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"


class TestPiParserSessionHeader:
    def test_session_header_yields_session_line(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "session",
                "version": 3,
                "id": "abc-123-uuid",
                "timestamp": "2025-01-01T00:00:00Z",
                "cwd": "/tmp/work",
            }
        )
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1
        assert results[0].type == "session"
        assert results[0].metadata.get("id") == "abc-123-uuid"


class TestPiParserAgentLifecycle:
    def test_agent_start_produces_no_output(self) -> None:
        parser = PiParser()
        line = _line({"type": "agent_start"})
        results = list(parser.parse(_lines(line)))
        assert results == []

    def test_agent_end_produces_stop(self) -> None:
        parser = PiParser()
        line = _line({"type": "agent_end", "messages": []})
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1
        assert results[0].type == "stop"


class TestPiParserMessageUpdateTextDelta:
    def test_text_delta_stream_accumulates_into_one_text_line(self) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": "Hello",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": " ",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": "world",
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        text_lines = [r for r in results if r.type == "text"]
        assert len(text_lines) == 1
        assert text_lines[0].content == "Hello world"

    def test_text_end_flushes_accumulator(self) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": "Hello",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_end",
                        "contentIndex": 0,
                        "content": "Hello",
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        text_lines = [r for r in results if r.type == "text"]
        assert len(text_lines) == 1
        assert text_lines[0].content == "Hello"

    def test_text_delta_with_empty_text_end_flushes_accumulator(self) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": "Hello",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_end",
                        "contentIndex": 0,
                        "content": "",
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        text_lines = [r for r in results if r.type == "text"]
        assert len(text_lines) == 1
        assert text_lines[0].content == "Hello"

    def test_text_delta_then_message_end_emits_exactly_one_text_line(self) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": "A",
                    },
                }
            ),
            _line(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "A"}],
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        text_lines = [r for r in results if r.type == "text"]
        assert len(text_lines) == 1
        assert text_lines[0].content == "A"

    def test_text_delta_text_end_message_end_emits_exactly_one_text_line(self) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": "Hello",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_end",
                        "contentIndex": 0,
                        "content": "Hello",
                    },
                }
            ),
            _line(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Hello"}],
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        text_lines = [r for r in results if r.type == "text"]
        assert len(text_lines) == 1
        assert text_lines[0].content == "Hello"

    def test_message_end_with_only_message_end_text_emits_one_text_line(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello"}],
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        text_lines = [r for r in results if r.type == "text"]
        assert len(text_lines) == 1
        assert text_lines[0].content == "Hello"

    def test_message_end_with_two_text_blocks_emits_both(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "First"},
                        {"type": "text", "text": "Second"},
                    ],
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        text_lines = [r for r in results if r.type == "text"]
        assert len(text_lines) == 2
        assert text_lines[0].content == "First"
        assert text_lines[1].content == "Second"

    def test_message_end_with_three_text_blocks_emits_all_three(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Alpha"},
                        {"type": "text", "text": "Beta"},
                        {"type": "text", "text": "Gamma"},
                    ],
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        text_lines = [r for r in results if r.type == "text"]
        assert len(text_lines) == 3
        assert [r.content for r in text_lines] == ["Alpha", "Beta", "Gamma"]

    def test_streaming_two_text_blocks_then_message_end_emits_each_once(
        self,
    ) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": "A",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_end",
                        "contentIndex": 0,
                        "content": "A",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 1,
                        "delta": "B",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_end",
                        "contentIndex": 1,
                        "content": "B",
                    },
                }
            ),
            _line(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "A"},
                            {"type": "text", "text": "B"},
                        ],
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        text_lines = [r for r in results if r.type == "text"]
        assert len(text_lines) == 2
        assert text_lines[0].content == "A"
        assert text_lines[1].content == "B"

    def test_interleaved_multi_index_text_streaming_flushes_each_block(
        self,
    ) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": "A",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 1,
                        "delta": "B",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_end",
                        "contentIndex": 0,
                        "content": "A",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_end",
                        "contentIndex": 1,
                        "content": "B",
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        text_lines = [r for r in results if r.type == "text"]
        assert len(text_lines) == 2
        assert text_lines[0].content == "A"
        assert text_lines[1].content == "B"
        assert not any(r.content == "AB" for r in results)

    def test_message_end_with_text_and_thinking_blocks_emits_each_block(
        self,
    ) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Reasoning"},
                        {"type": "text", "text": "Answer"},
                    ],
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        thinking_lines = [r for r in results if r.type == "thinking"]
        text_lines = [r for r in results if r.type == "text"]
        assert len(thinking_lines) == 1
        assert thinking_lines[0].content == "Reasoning"
        assert len(text_lines) == 1
        assert text_lines[0].content == "Answer"


class TestPiParserThinkingDelta:
    def test_thinking_delta_routes_to_thinking(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_update",
                "message": {"role": "assistant"},
                "assistantMessageEvent": {
                    "type": "thinking_delta",
                    "contentIndex": 0,
                    "delta": "Let me think...",
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        thinking_lines = [r for r in results if r.type == "thinking"]
        assert any(r.content == "Let me think..." for r in thinking_lines)

    def test_thinking_delta_then_thinking_end_emits_exactly_one_thinking_line(
        self,
    ) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "contentIndex": 1,
                        "delta": "think...",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "thinking_end",
                        "contentIndex": 1,
                        "content": "think...",
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        thinking_lines = [r for r in results if r.type == "thinking"]
        assert len(thinking_lines) == 1
        assert thinking_lines[0].content == "think..."

    def test_thinking_delta_with_empty_thinking_end_flushes_accumulator(
        self,
    ) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "contentIndex": 1,
                        "delta": "think",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "thinking_end",
                        "contentIndex": 1,
                        "content": "",
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        thinking_lines = [r for r in results if r.type == "thinking"]
        assert len(thinking_lines) == 1
        assert thinking_lines[0].content == "think"

    def test_thinking_delta_then_message_end_emits_exactly_one_thinking_line(
        self,
    ) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "contentIndex": 1,
                        "delta": "reasoning",
                    },
                }
            ),
            _line(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "thinking", "thinking": "reasoning"}],
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        thinking_lines = [r for r in results if r.type == "thinking"]
        assert len(thinking_lines) == 1
        assert thinking_lines[0].content == "reasoning"

    def test_message_end_with_two_thinking_blocks_emits_both(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "Plan A"},
                        {"type": "thinking", "thinking": "Plan B"},
                    ],
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        thinking_lines = [r for r in results if r.type == "thinking"]
        assert len(thinking_lines) == 2
        assert thinking_lines[0].content == "Plan A"
        assert thinking_lines[1].content == "Plan B"

    def test_message_end_emits_toolresult_block(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolResult",
                            "toolCallId": "x",
                            "result": "ok",
                        }
                    ],
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        tool_result_lines = [r for r in results if r.type == "tool_result"]
        assert len(tool_result_lines) == 1
        assert tool_result_lines[0].content == "ok"

    def test_message_end_emits_toolresult_block_normalizes_structured_result(
        self,
    ) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolResult",
                            "toolCallId": "x",
                            "result": {"content": [{"type": "text", "text": "ok"}]},
                        }
                    ],
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        tool_result_lines = [r for r in results if r.type == "tool_result"]
        assert len(tool_result_lines) == 1
        assert tool_result_lines[0].content == "ok"
        assert "{'content'" not in tool_result_lines[0].content
        assert "{'type'" not in tool_result_lines[0].content

    def test_message_end_emits_toolresult_block_error(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolResult",
                            "toolCallId": "x",
                            "result": "fail",
                            "isError": True,
                        }
                    ],
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        error_lines = [r for r in results if r.type == "error"]
        assert len(error_lines) == 1
        assert error_lines[0].content == "fail"
        assert not any(r.type == "tool_result" for r in results)

    def test_message_end_emits_toolcall_block(self) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "toolcall_end",
                        "contentIndex": 0,
                        "toolCall": {
                            "id": "c1",
                            "name": "bash",
                            "arguments": {},
                        },
                    },
                }
            ),
            _line(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "c1",
                    "toolName": "bash",
                }
            ),
            _line(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "toolCall", "id": "c1", "name": "bash"}],
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        tool_use_lines = [r for r in results if r.type == "tool_use"]
        assert len(tool_use_lines) == 3
        assert all(r.content == "bash" for r in tool_use_lines)
        assert not any(r.content == "unknown" for r in tool_use_lines)

    def test_interleaved_text_and_thinking_deltas(self) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": "Hi",
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "contentIndex": 1,
                        "delta": "reasoning",
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        types = {r.type for r in results}
        assert "text" in types
        assert "thinking" in types


class TestPiParserToolExecution:
    def test_tool_execution_start_yields_tool_use(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "tool_execution_start",
                "toolCallId": "call_1",
                "toolName": "bash",
                "args": {"command": "ls -la"},
            }
        )
        results = list(parser.parse(_lines(line)))
        tool_use_lines = [r for r in results if r.type == "tool_use"]
        assert len(tool_use_lines) == 1
        assert tool_use_lines[0].content == "bash"

    def test_tool_execution_update_does_not_emit_terminal_result(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "tool_execution_update",
                "toolCallId": "call_1",
                "toolName": "bash",
                "args": {"command": "ls -la"},
                "partialResult": {"content": [{"type": "text", "text": "partial"}]},
            }
        )
        results = list(parser.parse(_lines(line)))
        assert not results

    def test_tool_execution_end_success_yields_tool_result(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": "ok"}]},
                "isError": False,
            }
        )
        results = list(parser.parse(_lines(line)))
        assert any(r.type == "tool_result" for r in results)
        assert not any(r.type == "error" for r in results)

    def test_tool_execution_end_success_normalizes_structured_result_to_text(
        self,
    ) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": "ok"}]},
                "isError": False,
            }
        )
        results = list(parser.parse(_lines(line)))
        tool_result_lines = [r for r in results if r.type == "tool_result"]
        assert len(tool_result_lines) == 1
        assert tool_result_lines[0].content == "ok"
        assert "{'content'" not in tool_result_lines[0].content
        assert "{'type'" not in tool_result_lines[0].content

    def test_tool_execution_end_error_normalizes_structured_result_to_text(
        self,
    ) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": "fail"}]},
                "isError": True,
            }
        )
        results = list(parser.parse(_lines(line)))
        error_lines = [r for r in results if r.type == "error"]
        assert len(error_lines) == 1
        assert error_lines[0].content == "fail"
        assert "{'content'" not in error_lines[0].content
        assert "{'type'" not in error_lines[0].content

    def test_tool_execution_end_error_yields_error_line(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "tool_execution_end",
                "toolCallId": "call_1",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": "fail"}]},
                "isError": True,
            }
        )
        results = list(parser.parse(_lines(line)))
        error_lines = [r for r in results if r.type == "error"]
        assert len(error_lines) == 1
        assert not any(r.type == "tool_result" for r in results)


class TestPiParserExtensionError:
    def test_extension_error_yields_error(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "extension_error",
                "extensionPath": "/path/to/extension.ts",
                "event": "tool_call",
                "error": "boom-extension",
            }
        )
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom-extension"


class TestPiParserMessageUpdateErrorAndDone:
    def test_message_update_error_yields_error_line(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_update",
                "message": {"role": "assistant"},
                "assistantMessageEvent": {
                    "type": "error",
                    "reason": "aborted",
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "aborted"

    def test_message_update_done_yields_stop_line(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_update",
                "message": {"role": "assistant"},
                "assistantMessageEvent": {
                    "type": "done",
                    "stopReason": "stop",
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        assert any(r.type == "stop" for r in results)

    def test_message_update_done_length_surfaces_context_exhaustion(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_update",
                "message": {"role": "assistant"},
                "assistantMessageEvent": {
                    "type": "done",
                    "stopReason": "length",
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        assert any(r.type == "error" and "context length" in r.content for r in results)
        assert any(r.type == "stop" for r in results)


class TestPiParserMessageUpdateToolcall:
    def test_toolcall_start_emits_no_output(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_update",
                "message": {"role": "assistant"},
                "assistantMessageEvent": {
                    "type": "toolcall_start",
                    "contentIndex": 0,
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        assert results == []

    def test_toolcall_delta_emits_no_output(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_update",
                "message": {"role": "assistant"},
                "assistantMessageEvent": {
                    "type": "toolcall_delta",
                    "contentIndex": 0,
                    "delta": '{"command":',
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        assert results == []

    def test_toolcall_end_emits_single_tool_use_line(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_update",
                "message": {"role": "assistant"},
                "assistantMessageEvent": {
                    "type": "toolcall_end",
                    "contentIndex": 0,
                    "toolCall": {
                        "id": "call_1",
                        "name": "bash",
                        "arguments": {"command": "ls"},
                    },
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        tool_use_lines = [r for r in results if r.type == "tool_use"]
        assert len(tool_use_lines) == 1
        assert tool_use_lines[0].content == "bash"

    def test_toolcall_start_delta_end_emits_exactly_one_tool_use(self) -> None:
        parser = PiParser()
        lines = [
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "toolcall_start",
                        "contentIndex": 0,
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "toolcall_delta",
                        "contentIndex": 0,
                        "delta": '{"command":',
                    },
                }
            ),
            _line(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "toolcall_end",
                        "contentIndex": 0,
                        "toolCall": {
                            "id": "call_1",
                            "name": "bash",
                            "arguments": {"command": "ls"},
                        },
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        tool_use_lines = [r for r in results if r.type == "tool_use"]
        assert len(tool_use_lines) == 1
        assert tool_use_lines[0].content == "bash"
        assert not any(r.type == "tool_use" and r.content == "unknown" for r in results)

    def test_toolcall_end_without_toolcall_uses_unknown_name(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_update",
                "message": {"role": "assistant"},
                "assistantMessageEvent": {
                    "type": "toolcall_end",
                    "contentIndex": 0,
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        tool_use_lines = [r for r in results if r.type == "tool_use"]
        assert len(tool_use_lines) == 1
        assert tool_use_lines[0].content == "unknown"


class TestPiParserFlushAccumulators:
    def test_text_delta_without_end_is_flushed_on_iterator_exhaustion(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_update",
                "message": {"role": "assistant"},
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "contentIndex": 0,
                    "delta": "tail content",
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        text_lines = [r for r in results if r.type == "text"]
        assert any(r.content == "tail content" for r in text_lines)

    def test_flush_accumulators_explicit_call_drains(self) -> None:
        parser = PiParser()
        list(
            parser._dispatcher.dispatch(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "contentIndex": 0,
                        "delta": "buffered",
                    },
                },
                '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"buffered"}}',
            )
        )
        flushed = list(parser.flush_accumulators())
        text_lines = [r for r in flushed if r.type == "text"]
        assert any(r.content == "buffered" for r in text_lines)


class TestPiParserProviderFailure:
    def test_message_end_error_stop_reason_yields_error_line(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [],
                    "provider": "codex-pooler",
                    "model": "gpt-5.6-terra",
                    "stopReason": "error",
                    "errorMessage": "Connection error.",
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        errors = [r for r in results if r.type == "error"]
        assert len(errors) == 1
        assert "Connection error." in errors[0].content

    def test_message_end_without_error_stop_reason_yields_no_error(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "all good"}],
                    "stopReason": "stop",
                },
            }
        )
        results = list(parser.parse(_lines(line)))
        assert not [r for r in results if r.type == "error"]

    def test_turn_end_and_agent_end_do_not_duplicate_message_error(self) -> None:
        failed_message: dict[str, object] = {
            "role": "assistant",
            "content": [],
            "stopReason": "error",
            "errorMessage": "Connection error.",
        }
        parser = PiParser()
        results = list(
            parser.parse(
                _lines(
                    _line({"type": "message_end", "message": failed_message}),
                    _line(
                        {
                            "type": "turn_end",
                            "message": failed_message,
                            "toolResults": [],
                        }
                    ),
                    _line({"type": "agent_end", "messages": [failed_message], "willRetry": True}),
                )
            )
        )
        errors = [r for r in results if r.type == "error"]
        assert len(errors) == 1, f"expected exactly one error line, got {errors}"
        assert len([r for r in results if r.type == "stop"]) == 2

    def test_auto_retry_start_carries_human_readable_content(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "auto_retry_start",
                "attempt": 2,
                "maxAttempts": 3,
                "delayMs": 4000,
                "errorMessage": "Connection error.",
            }
        )
        results = list(parser.parse(_lines(line)))
        retry_lines = [r for r in results if r.type == "auto_retry_start"]
        assert len(retry_lines) == 1
        content = retry_lines[0].content
        assert content, "auto_retry_start must not emit an empty body"
        assert "2" in content
        assert "3" in content
        assert "Connection error." in content

    def test_auto_retry_end_failure_yields_error_line(self) -> None:
        parser = PiParser()
        line = _line(
            {
                "type": "auto_retry_end",
                "success": False,
                "attempt": 3,
                "finalError": "Connection error.",
            }
        )
        results = list(parser.parse(_lines(line)))
        errors = [r for r in results if r.type == "error"]
        assert len(errors) == 1
        assert "Connection error." in errors[0].content

    def test_auto_retry_end_success_yields_no_error_line(self) -> None:
        parser = PiParser()
        line = _line({"type": "auto_retry_end", "success": True, "attempt": 2})
        results = list(parser.parse(_lines(line)))
        assert not [r for r in results if r.type == "error"]

    def test_full_provider_outage_stream_surfaces_error(self) -> None:
        failed_message: dict[str, object] = {
            "role": "assistant",
            "content": [],
            "stopReason": "error",
            "errorMessage": "Connection error.",
        }
        raw: list[str] = []
        for attempt in (1, 2, 3):
            raw.extend(
                [
                    _line({"type": "agent_start"}),
                    _line({"type": "turn_start"}),
                    _line({"type": "message_start", "message": failed_message}),
                    _line({"type": "message_end", "message": failed_message}),
                    _line({"type": "turn_end", "message": failed_message, "toolResults": []}),
                    _line(
                        {
                            "type": "agent_end",
                            "messages": [failed_message],
                            "willRetry": attempt < 3,
                        }
                    ),
                ]
            )
            if attempt < 3:
                raw.append(
                    _line(
                        {
                            "type": "auto_retry_start",
                            "attempt": attempt + 1,
                            "maxAttempts": 3,
                            "delayMs": 4000,
                            "errorMessage": "Connection error.",
                        }
                    )
                )
        raw.append(
            _line(
                {
                    "type": "auto_retry_end",
                    "success": False,
                    "attempt": 3,
                    "finalError": "Connection error.",
                }
            )
        )
        results = list(PiParser().parse(_lines(*raw)))
        errors = [r for r in results if r.type == "error"]
        assert errors, "a fully failed provider run must not be silent"
        assert all("Connection error." in r.content for r in errors)
