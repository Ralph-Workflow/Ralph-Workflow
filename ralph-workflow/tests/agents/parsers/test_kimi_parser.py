from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers import NdjsonParserBase
from ralph.agents.parsers.kimi import KimiParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestKimiParserSubclassRelationship:
    def test_subclass_of_ndjson_base(self) -> None:
        assert issubclass(KimiParser, NdjsonParserBase)

    def test_has_parse_method(self) -> None:
        parser = KimiParser()
        assert callable(parser.parse)


class TestKimiParserSharedNdjsonBehaviors:
    def test_data_prefix_stripped(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    'data: {"role": "assistant", "content": "hi"}',
                ),
            )
        )
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hi"

    def test_done_sentinel_yields_stop(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines("[DONE]")))
        assert len(results) == 1
        assert results[0].type == "stop"

    def test_non_json_line_yields_raw(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines("not json at all")))
        assert len(results) == 1
        assert results[0].type == "raw"
        assert results[0].content == "not json at all"

    def test_non_dict_json_yields_raw(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines("[1, 2, 3]")))
        assert len(results) == 1
        assert results[0].type == "raw"

        results = list(parser.parse(_lines('"just a string"')))
        assert len(results) == 1
        assert results[0].type == "raw"

    def test_error_shape_yields_error(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line({"role": "assistant", "error": "boom"}))))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"


class TestKimiAssistantMessages:
    def test_string_content_yields_text_event(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line({"role": "assistant", "content": "hello world"}))))
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hello world"

    def test_array_content_yields_text_event(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "part one "},
                                {"type": "text", "text": "part two"},
                            ],
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "part one part two"

    def test_assistant_message_never_emits_stop(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(_lines(_line({"role": "assistant", "content": "done replying"})))
        )
        assert all(r.type != "stop" for r in results)

    def test_text_flushed_before_tool_call_in_same_message(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "role": "assistant",
                            "content": "running the command now",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "id": "call_1",
                                    "function": {
                                        "name": "bash",
                                        "arguments": json.dumps({"command": "echo hi"}),
                                    },
                                }
                            ],
                        }
                    )
                )
            )
        )
        types = [r.type for r in results]
        assert types == ["text", "tool_use"]
        assert results[0].content == "running the command now"
        assert results[1].metadata["tool"] == "bash"
        assert results[1].metadata["input"] == {"command": "echo hi"}
        assert results[1].metadata["tool_use_id"] == "call_1"

    def test_tool_call_summary_uses_leading_argument(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "id": "call_2",
                                    "function": {
                                        "name": "edit_file",
                                        "arguments": json.dumps(
                                            {"file_path": "src/main.py", "old": "a"}
                                        ),
                                    },
                                }
                            ],
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "tool_use"
        assert results[0].content == "edit_file src/main.py"

    def test_undecodable_arguments_degrade_to_empty_input(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "id": "call_3",
                                    "function": {
                                        "name": "bash",
                                        "arguments": "not json",
                                    },
                                }
                            ],
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "tool_use"
        assert results[0].metadata["tool"] == "bash"
        assert results[0].metadata["input"] == {}
        assert results[0].metadata["function"] == {
            "name": "bash",
            "arguments": "not json",
        }

    def test_dict_arguments_accepted_directly(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "id": "call_4",
                                    "function": {
                                        "name": "grep",
                                        "arguments": {"pattern": "def main"},
                                    },
                                }
                            ],
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].metadata["input"] == {"pattern": "def main"}
        assert results[0].content == "grep def main"

    def test_multiple_tool_calls_in_one_message(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "id": "call_a",
                                    "function": {
                                        "name": "bash",
                                        "arguments": json.dumps({"command": "ls"}),
                                    },
                                },
                                {
                                    "type": "function",
                                    "id": "call_b",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "README.md"}),
                                    },
                                },
                            ],
                        }
                    )
                )
            )
        )
        assert [r.type for r in results] == ["tool_use", "tool_use"]
        assert [r.metadata["tool_use_id"] for r in results] == ["call_a", "call_b"]
        assert [r.content for r in results] == ["bash ls", "read_file README.md"]

    def test_missing_function_key_yields_unknown_tool(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{"type": "function", "id": "call_5"}],
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "tool_use"
        assert results[0].metadata["tool"] == "unknown"
        assert results[0].content == "unknown"


class TestKimiToolMessages:
    def _tool_frame(self, **extra: object) -> dict[str, object]:
        frame: dict[str, object] = {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "hi\n",
        }
        frame.update(extra)
        return frame

    def test_tool_result_yields_tool_result_event(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line(self._tool_frame()))))
        assert len(results) == 1
        assert results[0].type == "tool_result"
        assert results[0].content == "hi\n"
        assert results[0].metadata["tool_use_id"] == "call_1"
        assert results[0].metadata["tool"] == "tool_call call_1"

    def test_tool_result_without_call_id(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line({"role": "tool", "content": "orphan result"}))))
        assert len(results) == 1
        assert results[0].type == "tool_result"
        assert results[0].metadata["tool"] == "tool_call"

    def test_tool_result_flushes_buffered_text(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line({"role": "assistant", "content": "preamble"}),
                    _line(self._tool_frame()),
                )
            )
        )
        assert [r.type for r in results] == ["text", "tool_result"]
        assert results[0].content == "preamble"

    def test_is_error_true_yields_error_event(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line(self._tool_frame(is_error=True, content="boom")))))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"

    def test_iserror_string_true_yields_error_event(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(_lines(_line(self._tool_frame(isError="true", content="kaboom"))))
        )
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "kaboom"

    def test_iserror_string_false_yields_tool_result(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line(self._tool_frame(isError="false")))))
        assert len(results) == 1
        assert results[0].type == "tool_result"

    def test_error_tool_frame_with_empty_content_gets_default_text(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line(self._tool_frame(is_error=True, content="")))))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "tool execution failed"


class TestKimiMetaFrames:
    def test_system_version_yields_lifecycle_with_banner(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(_line({"role": "meta", "type": "system.version", "version": "0.36.1"}))
            )
        )
        assert len(results) == 1
        assert results[0].type == "lifecycle"
        assert results[0].content == "kimi 0.36.1"

    def test_session_resume_hint_yields_lifecycle_with_session(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "role": "meta",
                            "type": "session.resume_hint",
                            "session_id": "sess-abc-1",
                            "command": "kimi -S sess-abc-1",
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "lifecycle"
        assert results[0].content == "kimi session sess-abc-1"
        assert results[0].metadata["session_id"] == "sess-abc-1"

    def test_system_version_prefers_message_content(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "role": "meta",
                            "type": "system.version",
                            "version": "0.36.1",
                            "content": "custom banner",
                        }
                    )
                )
            )
        )
        assert results[0].content == "custom banner"

    def test_unknown_meta_type_degrades_observably(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line({"role": "meta", "type": "telemetry.counter"}))))
        assert len(results) == 1
        assert results[0].type == "lifecycle"
        assert results[0].content == "kimi meta telemetry.counter"

    def test_meta_frame_without_type(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line({"role": "meta"}))))
        assert len(results) == 1
        assert results[0].type == "lifecycle"
        assert results[0].content == "kimi meta unknown"


class TestKimiUserAndUnknownRoles:
    def test_user_message_suppressed(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line({"role": "user", "content": "do the thing"}))))
        assert results == []

    def test_unknown_role_passes_through(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(_lines(_line({"role": "developer", "content": "future frame"})))
        )
        assert len(results) == 1
        assert results[0].type == "developer"

    def test_missing_role_passes_through_as_unknown(self) -> None:
        parser = KimiParser()
        results = list(parser.parse(_lines(_line({"content": "no role key"}))))
        assert len(results) == 1
        assert results[0].type == "unknown"


class TestKimiTextCoalescing:
    def test_two_messages_without_boundary_coalesce(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line({"role": "assistant", "content": "hello "}),
                    _line({"role": "assistant", "content": "world"}),
                )
            )
        )
        assert [r.type for r in results] == ["text"]
        assert results[0].content == "hello world"

    def test_paragraph_boundary_flushes(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line({"role": "assistant", "content": "para one\n\n"}),
                    _line({"role": "assistant", "content": "para two"}),
                )
            )
        )
        assert [r.type for r in results] == ["text", "text"]
        assert [r.content for r in results] == ["para one", "para two"]

    def test_exhaustion_flushes_remaining_text(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line({"role": "assistant", "content": "tail text"}),
                )
            )
        )
        assert [r.type for r in results] == ["text"]
        assert results[0].content == "tail text"

    def test_empty_stream_yields_nothing(self) -> None:
        parser = KimiParser()
        assert list(parser.parse(_lines())) == []


class TestKimiFullExchange:
    def test_full_exchange_order(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "role": "meta",
                            "type": "system.version",
                            "version": "0.36.1",
                        }
                    ),
                    _line(
                        {
                            "role": "meta",
                            "type": "session.resume_hint",
                            "session_id": "sess-full-1",
                            "command": "kimi -S sess-full-1",
                        }
                    ),
                    _line({"role": "assistant", "content": "I will check the file."}),
                    _line(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "id": "call_full_1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "PROMPT.md"}),
                                    },
                                }
                            ],
                        }
                    ),
                    _line(
                        {
                            "role": "tool",
                            "tool_call_id": "call_full_1",
                            "content": "file contents here",
                        }
                    ),
                    _line({"role": "assistant", "content": "All done."}),
                )
            )
        )
        assert [r.type for r in results] == [
            "lifecycle",
            "lifecycle",
            "text",
            "tool_use",
            "tool_result",
            "text",
        ]
