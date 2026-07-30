"""Migration test: OpenCodeParser must inherit from NdjsonParserBase.

After the consolidation refactor, OpenCodeParser inherits from
:class:`ralph.agents.parsers._ndjson_base.NdjsonParserBase` and delegates
the data: prefix strip, [DONE] short-circuit, json parse dispatch,
lifecycle suppression, and error extraction to the base layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.parsers import NdjsonParserBase
from ralph.agents.parsers.opencode import OpenCodeParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


class TestOpenCodeUsesNdjsonBase:
    """Pin subclass relationship and migration behavior preservation."""

    def test_opencode_parser_subclass_of_ndjson_base(self) -> None:
        assert issubclass(OpenCodeParser, NdjsonParserBase)

    def test_lifecycle_event_suppressed(self) -> None:
        parser = OpenCodeParser()
        results = list(parser.parse(_lines('{"type": "message_start"}')))
        assert results == []

    def test_error_field_yields_error_line(self) -> None:
        parser = OpenCodeParser()
        results = list(parser.parse(_lines('{"error": {"message": "boom"}}')))
        assert len(results) == 1
        assert results[0].type == "error"
        assert results[0].content == "boom"

    def test_done_event_yields_stop(self) -> None:
        parser = OpenCodeParser()
        results = list(parser.parse(_lines('{"type": "done"}')))
        assert len(results) == 1
        assert results[0].type == "stop"

    def test_stream_text_accumulates(self) -> None:
        parser = OpenCodeParser()
        results = list(
            parser.parse(
                _lines(
                    '{"type": "step_start", "id": "s1"}',
                    '{"type": "stream", "content": "Hello"}',
                    '{"type": "stream", "content": " world"}',
                    '{"type": "step_finish", "id": "s1"}',
                )
            )
        )
        text_results = [r for r in results if r.type == "text"]
        # The accumulated text from the stream + step_finish should yield
        # at least one "Hello world" text result.
        assert any(r.content == "Hello world" for r in text_results), (
            f"Expected 'Hello world' in {text_results!r}"
        )

    def test_errored_tool_still_surfaces_the_dispatch(self) -> None:
        """A tool whose state errored MUST stay visible as a dispatch.

        Emitting only the error erased the call from the tool timeline, so an
        errored ``task`` dispatch was reported as "subagent dispatch was not
        observed" even though the subagent was genuinely dispatched.
        """
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool",'
            ' "tool": "task", "callID": "call_1", "state": {"status": "error",'
            ' "input": {"prompt": "x"}, "error": "MCP error -32001: Request timed out"}}}'
        )

        results = list(parser.parse(_lines(line)))

        assert [r.type for r in results] == ["tool_use", "error"]
        assert results[0].content == "task"
        assert results[1].content == "MCP error -32001: Request timed out"

    def test_integer_epoch_timestamp_is_preserved(self) -> None:
        """OpenCode stamps events with an integer epoch-ms.

        The string-only source-timestamp branch skipped it, so every OpenCode
        record fell back to the display clock instead of the agent's own.
        """
        parser = OpenCodeParser()
        line = (
            '{"type": "text", "timestamp": 1785133508187, "sessionID": "ses_1",'
            ' "part": {"type": "text", "id": "prt_1", "text": "hi"}}'
        )

        results = list(parser.parse(_lines(line)))

        assert len(results) == 1
        assert results[0].timestamp is not None
        assert results[0].timestamp.startswith("2026-07-27T06:25:08")
