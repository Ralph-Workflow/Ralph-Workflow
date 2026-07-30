"""Tests for CursorParser \u2014 the Cursor Agent CLI ``--output-format stream-json`` parser.

CursorParser is a black-box NDJSON parser for the JSON-stream output of
``agent --print --output-format stream-json``.  The wire format is
the documented Cursor Agent stream-json envelope (per the Cursor CLI
``--help`` and the Cursor Agent streaming docs).

The parser inherits the 6 shared NDJSON behaviors from
:class:`NdjsonParserBase` and overrides ``_dispatch_json_object`` to
route Cursor's documented event vocabulary (``system`` / ``user`` /
``assistant`` / ``thinking`` / ``tool_call`` / ``tool_result`` /
``result``) to :class:`AgentOutputLine` types.  This module covers:

  - 6 shared NDJSON behaviors inherited from NdjsonParserBase
    (a) ``data:`` SSE prefix strip
    (b) ``[DONE]`` short-circuit -> ``type='stop'``
    (c) non-JSON line -> ``type='raw'``
    (d) non-dict JSON -> ``type='raw'``
    (e) ``{'error': ...}`` shapes -> ``type='error'``

  - cursor-specific event types
    (g) ``system`` event -> ``type='status'`` with the system message
    (h) ``user`` event -> no output (input echo, suppressed)
    (i) ``assistant`` event with text content block -> ``type='text'``
    (j) ``assistant`` event with thinking content block -> ``type='thinking'``
    (k) ``thinking`` event -> ``type='thinking'`` with the delta
    (l) ``tool_call`` event -> ``type='tool_use'`` with the tool name
    (m) ``tool_result`` event (success) -> ``type='tool_result'``
    (n) ``tool_result`` event (``is_error=true``) -> ``type='error'``
    (o) ``result`` event -> ``type='stop'`` (canonical completion)
    (p) parser flushes text/thinking accumulators on ``result``
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers import NdjsonParserBase
from ralph.agents.parsers.cursor import CursorParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestCursorParserSubclassRelationship:
    """CursorParser is a subclass of NdjsonParserBase and supports parse()."""

    def test_subclass_of_ndjson_base(self) -> None:
        assert issubclass(CursorParser, NdjsonParserBase)

    def test_has_parse_method(self) -> None:
        parser = CursorParser()
        assert callable(parser.parse)


class TestCursorParserSharedNdjsonBehaviors:
    """6 shared NDJSON behaviors inherited from NdjsonParserBase."""

    def test_data_prefix_stripped(self) -> None:
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    'data: {"type": "tool_call", "toolName": "bash", "args": {"cmd": "ls"}}',
                ),
            )
        )
        # The ``data:`` prefix must be stripped before JSON parse,
        # allowing the tool_call event to dispatch to a tool_use line.
        assert len(results) == 1
        assert results[0].type == "tool_use"
        assert results[0].content == "bash"

    def test_done_sentinel_yields_stop(self) -> None:
        parser = CursorParser()
        results = list(parser.parse(_lines("[DONE]")))
        assert len(results) == 1
        assert results[0].type == "stop"

    def test_non_json_line_yields_raw(self) -> None:
        parser = CursorParser()
        results = list(parser.parse(_lines("not json at all")))
        assert len(results) == 1
        assert results[0].type == "raw"
        assert results[0].content == "not json at all"

    def test_non_dict_json_yields_raw(self) -> None:
        parser = CursorParser()
        results = list(parser.parse(_lines("[1, 2, 3]")))
        assert len(results) == 1
        assert results[0].type == "raw"

        results = list(parser.parse(_lines('"just a string"')))
        assert len(results) == 1
        assert results[0].type == "raw"

    def test_error_shape_yields_error(self) -> None:
        parser = CursorParser()
        results = list(
            parser.parse(_lines(_line({"type": "x", "error": "boom"})))
        )
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"

    def test_lifecycle_events_routed_through_dispatch(self) -> None:
        """Cursor's documented event vocabulary includes ``user`` /
        ``assistant`` / ``thinking``, which the base class treats as
        lifecycle events.  CursorParser overrides the base lifecycle
        hook to fall through to ``_dispatch_json_object`` so those
        events reach the per-event handler map (where they surface as
        ``type='text'`` / ``type='thinking'`` / no-output for ``user``).
        """
        parser = CursorParser()
        # ``user`` events are input echo; the cursor dispatcher
        # suppresses them (no output).  This proves the lifecycle
        # hook fall-through reaches the dispatcher (otherwise the
        # base would still suppress via is_lifecycle_event).
        results = list(parser.parse(_lines(_line({"type": "user", "content": "echo"}))))
        assert results == []


class TestCursorParserWireFormat:
    """cursor-specific event types from the documented stream-json envelope."""

    def test_assistant_text_delta_yields_text_event(self) -> None:
        """``assistant`` event with a text content block yields a text event."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [
                                    {"type": "text", "text": "hello world"},
                                ],
                            },
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hello world"

    def test_assistant_thinking_block_yields_thinking_event(self) -> None:
        """``assistant`` event with a thinking content block yields a thinking event."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [
                                    {"type": "thinking", "thinking": "considering"},
                                ],
                            },
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "thinking"
        assert results[0].content == "considering"

    def test_assistant_tool_call_block_yields_tool_use(self) -> None:
        """``assistant`` event with a tool_call content block yields a tool_use line."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_call",
                                        "name": "bash",
                                        "args": {"cmd": "ls"},
                                    },
                                ],
                            },
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "tool_use"
        assert results[0].content == "bash"

    def test_system_event_yields_status(self) -> None:
        """``system`` event surfaces as ``type='status'`` with the message."""
        parser = CursorParser()
        results = list(
            parser.parse(_lines(_line({"type": "system", "message": "ready"})))
        )
        assert len(results) == 1
        assert results[0].type == "status"
        assert results[0].content == "ready"

    def test_user_event_is_suppressed(self) -> None:
        """``user`` event is the input echo (the prompt Ralph sent); suppressed."""
        parser = CursorParser()
        results = list(parser.parse(_lines(_line({"type": "user", "content": "echo"}))))
        assert results == []

    def test_thinking_event_yields_thinking(self) -> None:
        """``thinking`` event surfaces as ``type='thinking'`` with the delta."""
        parser = CursorParser()
        results = list(
            parser.parse(_lines(_line({"type": "thinking", "text": "pondering"})))
        )
        assert len(results) == 1
        assert results[0].type == "thinking"
        assert results[0].content == "pondering"

    def test_tool_call_event_yields_tool_use(self) -> None:
        """``tool_call`` event surfaces as ``type='tool_use'`` with the tool name."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "tool_call",
                            "toolName": "edit_file",
                            "args": {"path": "foo.py"},
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "tool_use"
        assert results[0].content == "edit_file"

    def test_live_tool_call_started_extracts_nested_tool_name(self) -> None:
        """Live Cursor streams nest the tool name under ``tool_call.<name>ToolCall``."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "tool_call",
                            "subtype": "started",
                            "call_id": "tool-1",
                            "tool_call": {
                                "editToolCall": {
                                    "args": {
                                        "path": "/tmp/probe/tool_probe.txt",
                                        "streamContent": "cursor parser probe",
                                    }
                                },
                                "toolCallId": "tool-1",
                            },
                        }
                    )
                )
            )
        )

        assert len(results) == 1
        assert results[0].type == "tool_use"
        assert results[0].content == "editToolCall"
        assert results[0].metadata["tool"] == "editToolCall"
        assert results[0].metadata["args"] == {
            "path": "/tmp/probe/tool_probe.txt",
            "streamContent": "cursor parser probe",
        }

    def test_live_mcp_tool_call_started_extracts_inner_mcp_tool(self) -> None:
        """Cursor's MCP wrapper should display the actual nested Ralph tool."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "tool_call",
                            "subtype": "started",
                            "call_id": "tool-1",
                            "tool_call": {
                                "mcpToolCall": {
                                    "args": {
                                        "name": "ralph-mcp__ralph__create_directory",
                                        "toolName": "mcp__ralph__create_directory",
                                        "args": {"path": "tmp/interactive-cursor-smoke"},
                                    }
                                },
                                "toolCallId": "tool-1",
                            },
                        }
                    )
                )
            )
        )

        assert len(results) == 1
        assert results[0].type == "tool_use"
        assert results[0].content == "mcp__ralph__create_directory"
        assert results[0].metadata["tool"] == "mcp__ralph__create_directory"
        assert results[0].metadata["args"] == {"path": "tmp/interactive-cursor-smoke"}

    def test_live_tool_call_completed_yields_tool_result_with_nested_tool_name(self) -> None:
        """Live Cursor uses ``tool_call`` + ``subtype=completed`` for tool results."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "tool_call",
                            "subtype": "completed",
                            "call_id": "tool-1",
                            "tool_call": {
                                "editToolCall": {
                                    "args": {
                                        "path": "/tmp/probe/tool_probe.txt",
                                        "streamContent": "cursor parser probe",
                                    },
                                    "result": {
                                        "success": {
                                            "path": "/tmp/probe/tool_probe.txt",
                                            "message": (
                                                "Wrote contents to /tmp/probe/tool_probe.txt"
                                            ),
                                        }
                                    },
                                },
                                "toolCallId": "tool-1",
                            },
                        }
                    )
                )
            )
        )

        assert len(results) == 1
        assert results[0].type == "tool_result"
        assert results[0].content == "Wrote contents to /tmp/probe/tool_probe.txt"
        assert results[0].metadata["tool"] == "editToolCall"

    def test_live_mcp_tool_call_completed_extracts_inner_result_text(self) -> None:
        """Cursor MCP result summaries should use the inner text payload."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "tool_call",
                            "subtype": "completed",
                            "call_id": "tool-1",
                            "tool_call": {
                                "mcpToolCall": {
                                    "args": {
                                        "name": "ralph-mcp__ralph__create_directory",
                                        "toolName": "mcp__ralph__create_directory",
                                        "args": {"path": "tmp/interactive-cursor-smoke"},
                                    },
                                    "result": {
                                        "success": {
                                            "content": [
                                                {
                                                    "text": {
                                                        "text": (
                                                            '{"path": '
                                                            '"tmp/interactive-cursor-smoke", '
                                                            '"created": true}'
                                                        )
                                                    }
                                                }
                                            ],
                                            "isError": False,
                                        }
                                    },
                                },
                                "toolCallId": "tool-1",
                            },
                        }
                    )
                )
            )
        )

        assert len(results) == 1
        assert results[0].type == "tool_result"
        assert results[0].content == '{"path": "tmp/interactive-cursor-smoke", "created": true}'
        assert results[0].metadata["tool"] == "mcp__ralph__create_directory"

    def test_live_stream_json_transcript_parses_all_semantic_output(self) -> None:
        """A live Cursor stream-json transcript surfaces every semantic event."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "system",
                            "subtype": "init",
                            "session_id": "cursor-session-1",
                            "model": "Auto",
                        }
                    ),
                    _line(
                        {
                            "type": "user",
                            "message": {
                                "role": "user",
                                "content": [{"type": "text", "text": "write file"}],
                            },
                            "session_id": "cursor-session-1",
                        }
                    ),
                    _line(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Creating `tool_probe.txt`.\n",
                                    }
                                ],
                            },
                            "session_id": "cursor-session-1",
                        }
                    ),
                    _line(
                        {
                            "type": "tool_call",
                            "subtype": "started",
                            "call_id": "tool-1",
                            "tool_call": {
                                "editToolCall": {
                                    "args": {
                                        "path": "/tmp/probe/tool_probe.txt",
                                        "streamContent": "cursor parser probe",
                                    }
                                },
                                "toolCallId": "tool-1",
                            },
                        }
                    ),
                    _line(
                        {
                            "type": "tool_call",
                            "subtype": "completed",
                            "call_id": "tool-1",
                            "tool_call": {
                                "editToolCall": {
                                    "args": {
                                        "path": "/tmp/probe/tool_probe.txt",
                                        "streamContent": "cursor parser probe",
                                    },
                                    "result": {
                                        "success": {
                                            "path": "/tmp/probe/tool_probe.txt",
                                            "message": (
                                                "Wrote contents to /tmp/probe/tool_probe.txt"
                                            ),
                                        }
                                    },
                                },
                                "toolCallId": "tool-1",
                            },
                        }
                    ),
                    _line(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Created `tool_probe.txt`.",
                                    }
                                ],
                            },
                            "session_id": "cursor-session-1",
                        }
                    ),
                    _line(
                        {
                            "type": "result",
                            "subtype": "success",
                            "is_error": False,
                            "session_id": "cursor-session-1",
                        }
                    ),
                )
            )
        )

        assert [(line.type, line.content) for line in results] == [
            ("status", "cursor session cursor-session-1 initialized with model Auto"),
            ("text", "Creating `tool_probe.txt`.\n"),
            ("tool_use", "editToolCall"),
            ("tool_result", "Wrote contents to /tmp/probe/tool_probe.txt"),
            ("text", "Created `tool_probe.txt`."),
            ("stop", ""),
        ]

    def test_tool_result_success_yields_tool_result(self) -> None:
        """``tool_result`` event (success path) surfaces as ``type='tool_result'``."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "tool_result",
                            "toolName": "edit_file",
                            "result": "ok",
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "tool_result"
        assert results[0].content == "ok"

    def test_tool_result_error_yields_error(self) -> None:
        """``tool_result`` event with ``is_error=true`` surfaces as ``type='error'``."""
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "tool_result",
                            "toolName": "edit_file",
                            "is_error": True,
                            "error": "permission denied",
                        }
                    )
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "permission denied"

    def test_result_event_yields_stop_and_flushes_accumulators(self) -> None:
        """``result`` event surfaces as ``type='stop'`` and flushes text/thinking accumulators.

        A buffered text block that has not yet hit a paragraph boundary
        must be emitted as the FINAL ``type='text'`` line BEFORE the
        ``type='stop'`` line, so the runtime sees the complete model
        response and the stop signal in the same iteration.
        """
        parser = CursorParser()
        results = list(
            parser.parse(
                _lines(
                    _line(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [
                                    {"type": "text", "text": "buffered response"},
                                ],
                            },
                        }
                    ),
                    _line({"type": "result"}),
                )
            )
        )
        # The buffered text surfaces first, then the stop signal.
        assert len(results) == 2
        assert results[0].type == "text"
        assert results[0].content == "buffered response"
        assert results[1].type == "stop"

    def test_unknown_event_passes_through_with_its_type(self) -> None:
        """Forward-compat: unknown event types pass through as their ``type`` field."""
        parser = CursorParser()
        results = list(
            parser.parse(_lines(_line({"type": "future_event", "data": "x"})))
        )
        assert len(results) == 1
        assert results[0].type == "future_event"
