"""Tests for KimiParser — the Kimi Code CLI ``--output-format stream-json`` parser.

KimiParser is a black-box NDJSON parser for the JSON-stream output of
``kimi -p <prompt> --output-format stream-json``.  The wire format is
the measured Kimi Code v0.36.1 Message envelope: one JSON object per
line keyed by ``role`` (``meta`` / ``assistant`` / ``tool`` / ``user``),
with tool activity carried in an assistant message's ``tool_calls``
list (JSON-string ``arguments``) and results in ``role:"tool"`` frames
keyed by ``tool_call_id``.

The parser inherits the shared NDJSON behaviors from
:class:`NdjsonParserBase` and overrides ``_dispatch_json_object`` to
route the role-keyed vocabulary to :class:`AgentOutputLine` types.
This module covers:

  - shared NDJSON behaviors inherited from NdjsonParserBase
    (a) ``data:`` SSE prefix strip
    (b) ``[DONE]`` short-circuit -> ``type='stop'``
    (c) non-JSON line -> ``type='raw'``
    (d) non-dict JSON -> ``type='raw'``
    (e) ``{'error': ...}`` shapes -> ``type='error'``

  - kimi-specific frame types
    (f) ``role:"assistant"`` with string content -> ``type='text'``
    (g) ``role:"assistant"`` with array content -> ``type='text'``
    (h) ``role:"assistant"`` with ``tool_calls`` -> text flush +
        ``type='tool_use'`` with decoded JSON-string arguments
    (i) ``role:"tool"`` (success) -> ``type='tool_result'`` with
        ``tool_use_id`` correlation
    (j) ``role:"tool"`` with ``is_error`` / ``isError`` -> ``type='error'``
    (k) ``role:"meta"`` ``system.version`` -> ``type='lifecycle'``
    (l) ``role:"meta"`` ``session.resume_hint`` -> ``type='lifecycle'``
    (m) ``role:"meta"`` unknown type -> observable ``type='lifecycle'``
    (n) ``role:"user"`` -> no output (input echo, suppressed)
    (o) unknown role -> passthrough with the role as the type
    (p) no ``stop`` on assistant messages: termination is process exit
        (iterator exhaustion) + ``flush_accumulators``
"""

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
    """KimiParser is a subclass of NdjsonParserBase and supports parse()."""

    def test_subclass_of_ndjson_base(self) -> None:
        assert issubclass(KimiParser, NdjsonParserBase)

    def test_has_parse_method(self) -> None:
        parser = KimiParser()
        assert callable(parser.parse)


class TestKimiParserSharedNdjsonBehaviors:
    """Shared NDJSON behaviors inherited from NdjsonParserBase."""

    def test_data_prefix_stripped(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(
                _lines(
                    'data: {"role": "assistant", "content": "hi"}',
                ),
            )
        )
        # The ``data:`` prefix must be stripped before JSON parse,
        # allowing the assistant message to dispatch to a text line.
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
    """``role:"assistant"`` messages surface text and tool_calls activity."""

    def test_string_content_yields_text_event(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(_lines(_line({"role": "assistant", "content": "hello world"})))
        )
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hello world"

    def test_array_content_yields_text_event(self) -> None:
        """Array-form content (documented for message envelopes) is accepted."""
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
        """The measured envelope has no terminal discriminator per message."""
        parser = KimiParser()
        results = list(
            parser.parse(_lines(_line({"role": "assistant", "content": "done replying"})))
        )
        assert all(r.type != "stop" for r in results)

    def test_text_flushed_before_tool_call_in_same_message(self) -> None:
        """Buffered text is a structural boundary: it flushes before the call."""
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
        """The tool_use content summarizes with file_path/path/command/query/pattern."""
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
        """A malformed JSON-string ``arguments`` keeps the call with an empty input."""
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
        # The raw entry is preserved verbatim in the metadata.
        assert results[0].metadata["function"] == {
            "name": "bash",
            "arguments": "not json",
        }

    def test_dict_arguments_accepted_directly(self) -> None:
        """A pre-decoded dict ``arguments`` value is used as-is."""
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
        """Every ``tool_calls`` entry surfaces as its own tool_use line."""
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
        """A tool_calls entry without a function dict surfaces as ``unknown``."""
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
    """``role:"tool"`` messages surface tool results and declared errors."""

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
        results = list(
            parser.parse(_lines(_line({"role": "tool", "content": "orphan result"})))
        )
        assert len(results) == 1
        assert results[0].type == "tool_result"
        assert results[0].metadata["tool"] == "tool_call"

    def test_tool_result_flushes_buffered_text(self) -> None:
        """A tool result is a structural boundary for buffered text."""
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
        results = list(
            parser.parse(_lines(_line(self._tool_frame(is_error=True, content="boom"))))
        )
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"

    def test_iserror_string_true_yields_error_event(self) -> None:
        """A string ``"true"`` is accepted (defensive against wrapper re-encoding)."""
        parser = KimiParser()
        results = list(
            parser.parse(_lines(_line(self._tool_frame(isError="true", content="kaboom"))))
        )
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "kaboom"

    def test_iserror_string_false_yields_tool_result(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(_lines(_line(self._tool_frame(isError="false"))))
        )
        assert len(results) == 1
        assert results[0].type == "tool_result"

    def test_error_tool_frame_with_empty_content_gets_default_text(self) -> None:
        parser = KimiParser()
        results = list(
            parser.parse(_lines(_line(self._tool_frame(is_error=True, content=""))))
        )
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "tool execution failed"


class TestKimiMetaFrames:
    """``role:"meta"`` frames surface as observable lifecycle events."""

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
        # The session id stays observable in the metadata for callers
        # that prefer the structured field.
        assert results[0].metadata["session_id"] == "sess-abc-1"

    def test_system_version_prefers_message_content(self) -> None:
        """A meta frame carrying explicit content uses it over the banner."""
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
        """A future meta type surfaces as a lifecycle line, never disappears."""
        parser = KimiParser()
        results = list(
            parser.parse(_lines(_line({"role": "meta", "type": "telemetry.counter"})))
        )
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
    """``role:"user"`` is suppressed; unknown roles pass through."""

    def test_user_message_suppressed(self) -> None:
        """user messages are the input echo of JSON-input mode."""
        parser = KimiParser()
        results = list(
            parser.parse(_lines(_line({"role": "user", "content": "do the thing"})))
        )
        assert results == []

    def test_unknown_role_passes_through(self) -> None:
        """A future Kimi role passes through with the role as the type."""
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
    """Text coalesces via the shared paragraph-boundary accumulator."""

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
    """A full measured-shape exchange parses end to end."""

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
