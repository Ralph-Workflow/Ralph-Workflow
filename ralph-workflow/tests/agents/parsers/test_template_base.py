"""Tests for the ParserTemplateBase template-method class."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.parsers.text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator


class _FixtureParser(ParserTemplateBase):
    """Minimal parser subclass for testing ParserTemplateBase."""

    _STOP_EVENT_TYPES = frozenset({"done"})

    def __init__(self) -> None:
        self._accumulators: dict[str, TextAccumulator] = {}

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        result = self.parse_json_line(line)
        if result is not None:
            yield result
            return
        # For test purposes, any valid JSON dict with a "text" field yields a text line
        obj = json.loads(line.strip())
        etype = obj.get("type")
        if etype == "accumulate":
            key = str(obj.get("key", "default"))
            val = str(obj.get("text", ""))
            if key not in self._accumulators:
                self._accumulators[key] = TextAccumulator()
            yield from self._accumulators[key].accumulate(
                val, line, kind="text", keep_current_when_empty=True
            )
        elif etype == "done":
            yield AgentOutputLine(type="stop", raw=line)
        elif "text" in obj:
            yield AgentOutputLine(type="text", content=str(obj["text"]), raw=line)


def _make_lines(data: list[str]) -> Iterator[str]:
    return iter(data)


class TestParseJsonLine:
    """Tests for ParserTemplateBase.parse_json_line."""

    def test_valid_json_dict(self) -> None:
        parser = _FixtureParser()
        result = parser.parse_json_line('{"type":"text","content":"hello"}')
        assert result is None  # Valid JSON dict -> None from default _classify_json_object

    def test_invalid_json_returns_raw(self) -> None:
        parser = _FixtureParser()
        result = parser.parse_json_line("not json")
        assert result is not None
        assert result.type == "raw"
        assert result.content == "not json"

    def test_non_dict_json_returns_raw(self) -> None:
        parser = _FixtureParser()
        result = parser.parse_json_line('"just a string"')
        assert result is not None
        assert result.type == "raw"
        assert result.content == '"just a string"'

    def test_json_number_returns_raw(self) -> None:
        parser = _FixtureParser()
        result = parser.parse_json_line("42")
        assert result is not None
        assert result.type == "raw"
        assert result.content == "42"


class TestIsStopEvent:
    """Tests for ParserTemplateBase.is_stop_event."""

    def test_known_stop_event(self) -> None:
        parser = _FixtureParser()
        assert parser.is_stop_event("done") is True

    def test_unknown_stop_event(self) -> None:
        parser = _FixtureParser()
        assert parser.is_stop_event("unknown") is False

    def test_empty_string(self) -> None:
        parser = _FixtureParser()
        assert parser.is_stop_event("") is False


class TestFlushAccumulators:
    """Tests for ParserTemplateBase.flush_accumulators."""

    def test_empty_accumulators_yields_nothing(self) -> None:
        parser = _FixtureParser()
        results = list(parser.flush_accumulators())
        assert results == []

    def test_flushes_single_accumulator(self) -> None:
        parser = _FixtureParser()
        text_acc = TextAccumulator()
        text_acc.buffer = "Hello World"
        text_acc.raw_lines = ['{"text":"Hello"}', '{"text":" World"}']
        parser._accumulators["k1"] = text_acc
        results = list(parser.flush_accumulators())
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "Hello World"

    def test_flushes_multiple_accumulators(self) -> None:
        parser = _FixtureParser()
        acc1 = TextAccumulator()
        acc1.buffer = "First"
        acc1.raw_lines = ['raw1']
        acc2 = TextAccumulator()
        acc2.buffer = "Second"
        acc2.raw_lines = ['raw2']
        parser._accumulators["k1"] = acc1
        parser._accumulators["k2"] = acc2
        results = list(parser.flush_accumulators())
        assert len(results) == 2
        contents = {r.content for r in results}
        assert contents == {"First", "Second"}

    def test_removes_accumulators_after_flush(self) -> None:
        parser = _FixtureParser()
        acc = TextAccumulator()
        acc.buffer = "content"
        parser._accumulators["k1"] = acc
        list(parser.flush_accumulators())
        assert len(parser._accumulators) == 0


class TestParseTemplate:
    """Tests for ParserTemplateBase.parse template method."""

    def test_empty_lines(self) -> None:
        parser = _FixtureParser()
        results = list(parser.parse(_make_lines([])))
        assert results == []

    def test_invalid_json_lines(self) -> None:
        parser = _FixtureParser()
        results = list(parser.parse(_make_lines(["not json", "also not json"])))
        assert len(results) == 2
        assert all(r.type == "raw" for r in results)

    def test_valid_json_lines(self) -> None:
        parser = _FixtureParser()
        results = list(
            parser.parse(_make_lines(['{"text":"hello"}', '{"text":"world"}']))
        )
        assert len(results) == 2
        assert all(r.type == "text" for r in results)
        assert results[0].content == "hello"
        assert results[1].content == "world"

    def test_accumulator_flush_on_iterator_end(self) -> None:
        parser = _FixtureParser()
        results = list(
            parser.parse(
                _make_lines(
                    [
                        '{"type":"accumulate","key":"k1","text":"Hello"}',
                        '{"type":"accumulate","key":"k1","text":" World"}',
                    ]
                )
            )
        )
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "Hello World"


class TestStopEventTypesDefault:
    """Test that the default _STOP_EVENT_TYPES is empty."""

    def test_default_empty(self) -> None:
        parser = _FixtureParser()
        assert frozenset({"done"}) == parser._STOP_EVENT_TYPES
        assert parser.is_stop_event("done") is True
        assert parser.is_stop_event("stop") is False
