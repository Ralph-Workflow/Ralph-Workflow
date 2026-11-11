from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers._ndjson_base import NdjsonParserBase
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator


class _RecordingParser(NdjsonParserBase):
    def __init__(self) -> None:
        super().__init__()
        self.received: list[tuple[dict[str, object], str]] = []

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        self.received.append((obj, raw))
        event_type = str(obj.get("type", "unknown"))
        if event_type == "text":
            content = str(obj.get("content", ""))
            yield AgentOutputLine(type="text", content=content, raw=raw, metadata=obj)
            return
        if event_type == "tool_use":
            tool_name = str(obj.get("name", "unknown"))
            yield AgentOutputLine(type="tool_use", content=tool_name, raw=raw, metadata=obj)
            return
        yield AgentOutputLine(type=event_type, raw=raw, metadata=obj)


class _PlainToolParser(NdjsonParserBase):
    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        if stripped.startswith("[plain] tool:"):
            return
        yield from super()._classify_non_json_line(stripped)


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


_STRIPPED_PREFIX_CASES: tuple[tuple[str, str], ...] = (
    ('data: {"type": "text", "content": "hi"}', "hi"),
    ('   data:    {"type": "text", "content": "x"}   ', "x"),
)

_NON_JSON_CASES: tuple[str, ...] = (
    "not json at all",
    "not json",
)

_NON_DICT_JSON_CASES: tuple[str, ...] = (
    "[1, 2, 3]",
    '"just a string"',
    "42",
)

_LIFECYCLE_TYPES: tuple[str, ...] = (
    "message_start",
    "message_stop",
    "content_block_start",
    "content_block_stop",
    "message_delta",
    "thread.started",
    "turn.started",
    "message_started",
    "heartbeat",
    "ping",
    "ready",
    "start",
    "begin",
    "user",
    "assistant",
    "thinking",
)

_ERROR_CASES: tuple[tuple[str, str], ...] = (
    (json.dumps({"error": {"message": "boom"}}), "boom"),
    (json.dumps({"error": "raw-fail"}), "raw-fail"),
)

_BLANK_CASES: tuple[str, ...] = (
    "",
    "   \t  ",
)


class TestNdjsonParserBase:
    def test_strips_data_prefix_before_json_parsing(self) -> None:
        for raw_line, expected_content in _STRIPPED_PREFIX_CASES:
            parser = _RecordingParser()
            results = list(parser.parse(_lines(raw_line)))

            assert [line.type for line in results] == ["text"], (
                f"{raw_line!r} must yield exactly one text line; got {results!r}"
            )
            assert results[0].content == expected_content, (
                f"{raw_line!r} must yield content {expected_content!r}; got {results[0].content!r}"
            )
            assert [obj.get("type") for obj, _ in parser.received] == ["text"], (
                f"{raw_line!r} must dispatch exactly one text object; got {parser.received!r}"
            )
            dispatched_raw = parser.received[0][1]
            assert "data:" not in dispatched_raw, (
                f"the dispatched raw payload must be prefix-free; got {dispatched_raw!r}"
            )

    def test_done_sentinel_yields_stop(self) -> None:
        parser = _RecordingParser()
        results = list(parser.parse(_lines("[DONE]")))

        assert [line.type for line in results] == ["stop"], (
            f"[DONE] must yield exactly one stop line; got {results!r}"
        )
        assert parser.received == []

    def test_non_json_line_yields_raw(self) -> None:
        for raw_line in _NON_JSON_CASES:
            parser = _RecordingParser()
            results = list(parser.parse(_lines(raw_line)))

            assert [line.type for line in results] == ["raw"], (
                f"{raw_line!r} must yield exactly one raw line; got {results!r}"
            )
            assert results[0].content == raw_line, (
                f"{raw_line!r} must keep its text as content; got {results[0].content!r}"
            )
            assert parser.received == []

    def test_non_dict_json_yields_raw(self) -> None:
        for raw_line in _NON_DICT_JSON_CASES:
            parser = _RecordingParser()
            results = list(parser.parse(_lines(raw_line)))

            assert [line.type for line in results] == ["raw"], (
                f"{raw_line!r} must yield exactly one raw line; got {results!r}"
            )
            assert results[0].content == raw_line, (
                f"{raw_line!r} must keep its text as content; got {results[0].content!r}"
            )
            assert parser.received == []

    def test_lifecycle_events_are_suppressed(self) -> None:
        for lifecycle_type in _LIFECYCLE_TYPES:
            parser = _RecordingParser()
            line = json.dumps({"type": lifecycle_type})
            results = list(parser.parse(_lines(line)))

            assert results == [], (
                f"Lifecycle event type {lifecycle_type!r} must be suppressed; got {results!r}"
            )
            assert parser.received == []

    def test_error_payload_produces_error_line(self) -> None:
        for raw_line, expected_content in _ERROR_CASES:
            parser = _RecordingParser()
            results = list(parser.parse(_lines(raw_line)))

            assert [line.type for line in results] == ["error"], (
                f"{raw_line!r} must yield exactly one error line; got {results!r}"
            )
            assert results[0].content == expected_content, (
                f"{raw_line!r} must carry the extracted message "
                f"{expected_content!r}; got {results[0].content!r}"
            )
            assert parser.received == []

    def test_dispatches_normal_event_to_subclass(self) -> None:
        parser = _RecordingParser()
        line = json.dumps({"type": "text", "content": "hello"})
        results = list(parser.parse(_lines(line)))

        assert [line.type for line in results] == ["text"], (
            f"a normal event must yield exactly one text line; got {results!r}"
        )
        assert results[0].content == "hello"
        assert [obj.get("type") for obj, _ in parser.received] == ["text"], (
            f"the subclass hook must receive the parsed object; got {parser.received!r}"
        )
        dispatched_raw = parser.received[0][1]
        assert dispatched_raw == line

    def test_inherits_parser_template_base(self) -> None:
        assert issubclass(NdjsonParserBase, ParserTemplateBase)

    def test_default_flush_is_noop(self) -> None:
        parser = _RecordingParser()
        results = list(parser.flush_accumulators())
        assert results == []

    def test_subclass_can_override_non_json_classification(self) -> None:
        parser = _PlainToolParser()
        results = list(parser.parse(_lines("[plain] tool: bash")))

        assert results == [], f"the subclass override must own the classification; got {results!r}"

        fallback = list(parser.parse(_lines("not json")))
        assert [line.type for line in fallback] == ["raw"], (
            f"an unrecognised non-JSON line must still yield one raw line; got {fallback!r}"
        )
        assert fallback[0].content == "not json"

    def test_blank_line_is_skipped(self) -> None:
        for raw_line in _BLANK_CASES:
            parser = _RecordingParser()
            results = list(parser.parse(_lines(raw_line)))
            assert results == [], f"blank line {raw_line!r} must be skipped; got {results!r}"
