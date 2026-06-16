"""Tests for PiParser — pi.dev AgentSessionEvent NDJSON parser.

PiParser is a black-box NDJSON parser for the JSON-stream output of
``pi --mode json <prompt>``.  The wire format is the documented
``AgentSessionEvent`` union at https://pi.dev/docs/latest/json.  The
only pi event that overlaps with the shared
:data:`LIFECYCLE_EVENT_TYPES` frozenset is ``message_start``, which
PiParser handles by overriding :meth:`_handle_lifecycle_event` to
fall through to ``_dispatch_json_object`` for every event type, then
marking ``message_start`` silent in the dispatcher.  This keeps the
AC-04 invariant (every documented pi event reaches
``_dispatch_json_object``) and pins the ``message_start`` -> no
output behavior via a separate test that drives a
``message_start`` event and asserts ``results == []``.

This test module covers:

  - 6 shared NDJSON behaviors inherited from NdjsonParserBase
    (a) ``data:`` SSE prefix strip
    (b) ``[DONE]`` short-circuit -> ``type='stop'``
    (c) non-JSON line -> ``type='raw'``
    (d) non-dict JSON -> ``type='raw'``
    (e) ``{'error': ...}`` shapes -> ``type='error'``

  - pi-specific event types
    (g) session header line -> ``type='session'`` with ``metadata['id']``
    (h) ``agent_start`` -> no output
    (i) ``agent_end`` -> one ``type='stop'``
    (j) ``message_update`` text_delta stream accumulates into one ``text``
    (k) interleaved text_delta and thinking_delta route to separate
        accumulators
    (l) ``tool_execution_start`` -> ``type='tool_use'`` with tool name
    (m) ``tool_execution_end`` with ``isError=false`` -> ``type='tool_result'``
    (n) ``tool_execution_end`` with ``isError=true`` -> ``type='error'``
    (o) ``extension_error`` -> ``type='error'`` with the error string
    (p) ``message_update`` with ``assistantMessageEvent.type == 'error'``
        and ``reason='aborted'`` -> ``type='error'`` with content='aborted'
    (q) ``message_update`` with ``assistantMessageEvent.type == 'done'``
        and ``stopReason='stop'`` -> ``type='stop'``
    (r) parser flushes all accumulators on iterator exhaustion
"""

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
    """PiParser is a subclass of NdjsonParserBase and supports parse()."""

    def test_subclass_of_ndjson_base(self) -> None:
        assert issubclass(PiParser, NdjsonParserBase)

    def test_has_parse_method(self) -> None:
        parser = PiParser()
        assert callable(parser.parse)


class TestPiParserSharedNdjsonBehaviors:
    """6 shared NDJSON behaviors inherited from NdjsonParserBase."""

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
        # The ``data:`` prefix must be stripped before JSON parse, allowing
        # the tool_execution_start event to dispatch to a tool_use line.
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
        """``message_start`` is the only pi event in LIFECYCLE_EVENT_TYPES.

        PiParser overrides :meth:`_handle_lifecycle_event` to fall
        through to :meth:`_dispatch_json_object` for every event type,
        honoring the AC-04 invariant.  The dispatcher then marks
        ``message_start`` silent (via
        :data:`_PI_SILENT_TOP_LEVEL_EVENTS`), so the observable
        behavior is still no output.
        """
        parser = PiParser()
        line = _line({"type": "message_start", "message": {"role": "assistant"}})
        results = list(parser.parse(_lines(line)))
        assert results == []

    def test_message_start_reaches_dispatch(self) -> None:
        """``message_start`` must reach ``_dispatch_json_object`` (AC-04).

        Uses a recording :class:`PiParser` subclass that appends every
        ``_dispatch_json_object`` argument to a list before delegating
        to the real implementation.  When a ``message_start`` event is
        parsed, the recording list must contain a ``message_start``
        entry, proving the event reached ``_dispatch_json_object`` (the
        AC-04 invariant).
        """
        seen_event_types: list[str] = []

        class _RecordingPiParser(PiParser):
            def _dispatch_json_object(
                self,
                obj: dict[str, object],
                raw: str,
            ) -> Iterator[AgentOutputLine]:
                seen_event_types.append(str(obj.get("type", "")))
                yield from super()._dispatch_json_object(obj, raw)

        parser = _RecordingPiParser()
        line = _line({"type": "message_start", "message": {"role": "assistant"}})
        results = list(parser.parse(_lines(line)))

        assert results == []
        # The dispatcher was called for the message_start event,
        # proving it reached _dispatch_json_object (the AC-04 invariant).
        assert "message_start" in seen_event_types

    def test_error_field_produces_error_line(self) -> None:
        parser = PiParser()
        line = _line({"error": {"message": "boom"}})
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"


class TestPiParserSessionHeader:
    """The session header line is the first line of --mode json output."""

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
    """agent_start is silent; agent_end is a stop marker."""

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
    """message_update text_delta streams accumulate into a single text line."""

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
        """``text_end`` carries the full content; emit exactly one text line.

        Regression test for the analysis feedback: the parser used to
        flush the buffered text deltas AND re-emit the ``text_end``
        content, producing two ``type='text'`` lines for the same
        logical content.  The terminal snapshot (``text_end.content``)
        is the authoritative final text; the parser must emit it
        exactly once.
        """
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
        """``text_delta + text_end(content="")`` must flush the buffered deltas.

        Regression test for the analysis feedback: the parser used to
        discard the accumulated deltas when ``text_end`` arrived with
        an empty ``content`` field.  The fix is: ``text_end`` with
        empty content falls back to the buffered accumulator; the
        buffered deltas are the source of truth when the event does
        not carry a final snapshot.
        """
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
        """``text_delta`` + ``message_end(text=X)`` must emit exactly one text line.

        Regression test for the analysis feedback: the parser used to
        buffer the delta and then re-emit the message.content text
        block, producing two ``type='text'`` lines.  The
        ``message_end`` snapshot is the authoritative final text; the
        parser must emit it exactly once.
        """
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
        """``text_delta + text_end(X) + message_end(text=X)`` -> one text line.

        Regression test: when both ``text_end`` and ``message_end`` carry
        the same content, the parser must not double-emit.  The
        ``text_end`` snapshot is authoritative; the ``message_end``
        snapshot is a redundant terminal snapshot and is suppressed.
        """
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
        """``message_end(text=X)`` alone (no streaming deltas) -> one text line."""
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


class TestPiParserThinkingDelta:
    """thinking_delta streams accumulate separately from text."""

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
        """``thinking_delta + thinking_end(X)`` -> exactly one thinking line.

        Regression test for the analysis feedback: the parser used to
        flush the buffered deltas AND re-emit the ``thinking_end``
        content, producing two ``type='thinking'`` lines.
        """
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
        """``thinking_delta + thinking_end(content="")`` must flush the buffered deltas.

        Regression test for the analysis feedback: the parser used to
        discard the accumulated thinking deltas when ``thinking_end``
        arrived with an empty ``content`` field.  The fix is:
        ``thinking_end`` with empty content falls back to the buffered
        accumulator.
        """
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
        """``thinking_delta + message_end(thinking=X)`` -> exactly one thinking line."""
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
                        "content": [
                            {"type": "thinking", "thinking": "reasoning"}
                        ],
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        thinking_lines = [r for r in results if r.type == "thinking"]
        assert len(thinking_lines) == 1
        assert thinking_lines[0].content == "reasoning"

    def test_message_end_emits_toolresult_block(self) -> None:
        """``message_end(toolResult)`` must emit a ``tool_result`` line.

        Per the plan, the ``message_end`` content array is walked for
        text, thinking, toolCall, and toolResult blocks.  toolResult
        blocks are emitted as ``type='tool_result'`` (or
        ``type='error'`` if ``isError=true``) using the same consistent
        rule as ``tool_execution_end``.
        """
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

    def test_message_end_emits_toolresult_block_error(self) -> None:
        """``message_end(toolResult, isError=true)`` must emit ``type='error'``.

        Single consistent ``isError`` rule: ``isError=True`` maps to
        ``type='error'`` regardless of whether the result is delivered
        via ``tool_execution_end`` or a ``toolResult`` block in
        ``message_end``.
        """
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
        """``message_end(toolCall)`` must emit a ``tool_use`` line.

        Per the plan, the ``message_end`` content array is walked for
        text, thinking, and toolCall blocks; the toolCall block is
        ALWAYS emitted.  For the
        ``toolcall_end + tool_execution_start + message_end(toolCall)``
        sequence the parser yields three ``type='tool_use'`` lines
        (one from each event), all with the resolved tool name.
        """
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
                        "content": [
                            {"type": "toolCall", "id": "c1", "name": "bash"}
                        ],
                    },
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        tool_use_lines = [r for r in results if r.type == "tool_use"]
        assert len(tool_use_lines) == 3
        assert all(r.content == "bash" for r in tool_use_lines)
        # No 'unknown' placeholder from any event in the sequence.
        assert not any(r.content == "unknown" for r in tool_use_lines)

    def test_interleaved_text_and_thinking_deltas(self) -> None:
        """text_delta and thinking_delta route to separate accumulators."""
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
    """tool_execution_start / update / end map to tool_use / tool_result / error."""

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

    def test_tool_execution_update_yields_tool_result(self) -> None:
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
        tool_result_lines = [r for r in results if r.type == "tool_result"]
        assert len(tool_result_lines) == 1

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

    def test_tool_execution_end_error_yields_error_line(self) -> None:
        """Single consistent isError=True -> type='error' rule."""
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
    """``extension_error`` events yield a single error line."""

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
    """message_update.assistantMessageEvent error and done sub-types."""

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


class TestPiParserMessageUpdateToolcall:
    """``message_update`` toolcall_start/delta/end must emit a single tool_use line.

    pi's assistantMessageEvent carries a streaming tool call:
      - toolcall_start: opens the tool call (no content yet)
      - toolcall_delta: appends partial argument text
      - toolcall_end: closes the tool call, carrying the final
        ``toolCall = { id, name, arguments }`` payload

    The parser must suppress ``toolcall_start`` and ``toolcall_delta``
    and emit exactly ONE ``type='tool_use'`` line on ``toolcall_end``,
    using the final ``toolCall.name``.  This pins the single-emission
    invariant for the streaming toolcall sequence.
    """

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
        """A full toolcall_start -> toolcall_delta -> toolcall_end sequence
        must emit exactly ONE tool_use line, using the final tool name from
        the ``toolcall_end.toolCall.name`` payload.  Intermediate events
        must NOT emit placeholder 'unknown' tool_use lines.
        """
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
        # No intermediate 'unknown' placeholder tool_use lines.
        assert not any(
            r.type == "tool_use" and r.content == "unknown" for r in results
        )

    def test_toolcall_end_without_toolcall_uses_unknown_name(self) -> None:
        """If ``toolcall_end`` arrives without a ``toolCall`` payload
        (defensive case), the parser still emits a single tool_use line
        with content='unknown' rather than dropping the event silently.
        """
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
    """flush_accumulators drains pending text/thinking buffers."""

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
        # The text_delta is buffered; on iterator exhaustion, the parser
        # must flush the buffer so the consumer still sees the content.
        text_lines = [r for r in results if r.type == "text"]
        assert any(r.content == "tail content" for r in text_lines)

    def test_flush_accumulators_explicit_call_drains(self) -> None:
        """``flush_accumulators()`` drains buffered text without needing parse() to end.

        The test seeds the parser's text accumulator via a normal
        text_delta line, then verifies the manual ``flush_accumulators()``
        call drains the buffer.
        """
        parser = PiParser()
        # Directly drive the dispatcher with one text_delta to populate
        # the text accumulator; ``text_delta`` does not yield until the
        # next flush, so the parser's internal buffer has "buffered" but
        # no AgentOutputLine was emitted.
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
