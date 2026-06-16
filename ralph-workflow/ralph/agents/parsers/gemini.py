"""Parser for Gemini's SSE+JSON streaming format.

Gemini emits Server-Sent Events (SSE) where each event contains a JSON payload.
This parser handles the SSE format and normalizes Gemini output to AgentOutputLine
instances with robust delta accumulation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message
from .text_accumulator import TextAccumulator

JsonValue = object
JsonDict = dict[str, JsonValue]

if TYPE_CHECKING:
    from collections.abc import Iterator


class _GeminiDispatch:
    """Per-event-type dispatch for GeminiParser.

    Encapsulates the historical event-routing logic that used to live in
    ``_parse_object``.  The subclass ``_dispatch_json_object`` delegates
    here for all non-lifecycle, non-error events.  The dispatcher holds
    a reference to the owning parser so accumulator state stays in one place.
    """

    def __init__(self, owner: GeminiParser) -> None:
        self._owner = owner

    def dispatch(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", obj.get("event", "unknown")))

        if event_type in GeminiParser._STOP_EVENT_TYPES:
            yield from self._flush()
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

        if event_type in ("text", "content"):
            yield from self._parse_text_content(obj, stripped)
        elif event_type in ("block", "content_block"):
            yield from self._parse_block(obj, stripped)
        elif event_type in ("tool_call", "tool_use"):
            yield from self._parse_tool_call(obj, stripped)
        elif event_type in ("tool_result", "function_call"):
            yield from self._parse_tool_result(obj, stripped)
        elif event_type in ("error", "error_details"):
            yield from self._parse_error(obj, stripped)
        elif event_type in ("candidate", "prompt_feedback"):
            yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)
        elif event_type in ("message", "server_message"):
            yield from self._parse_message(obj, stripped)
        elif "candidates" in obj:
            yield from self._parse_candidates_response(obj, stripped)
        else:
            yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _flush(self) -> Iterator[AgentOutputLine]:
        if self._owner._text_accumulator is None:
            return
        acc = self._owner._text_accumulator
        self._owner._text_accumulator = None
        yield from acc.flush(kind="text")

    def _parse_text_content(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        content = self._extract_first_part_text(obj)
        if not content:
            content = str(obj.get("content", "") or obj.get("text", ""))

        if not content:
            return

        if self._owner._text_accumulator is None:
            self._owner._text_accumulator = TextAccumulator()
        yield from self._owner._text_accumulator.accumulate(
            content, stripped, kind="text", keep_current_when_empty=True
        )

    def _parse_block(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        content = self._extract_first_part_text(obj)
        if not content:
            content = str(obj.get("content", ""))

        if not content:
            return

        if self._owner._text_accumulator is None:
            self._owner._text_accumulator = TextAccumulator()
        yield from self._owner._text_accumulator.accumulate(
            content, stripped, kind="text", keep_current_when_empty=True
        )

    def _parse_tool_call(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        function_call_obj: JsonValue | None = obj.get("function_call")
        func_call: JsonDict | None = (
            cast("JsonDict", function_call_obj) if isinstance(function_call_obj, dict) else None
        )
        tool_name = str(
            obj.get("name", "")
            or obj.get("tool", "")
            or (func_call.get("name", "") if func_call is not None else "")
        )
        args_source = obj.get("args") or obj.get("arguments")
        args_str = ""
        if args_source:
            args_str = str(args_source)
        elif func_call is not None:
            func_args = func_call.get("args")
            if isinstance(func_args, dict):
                args_str = str(cast("JsonDict", func_args))
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "args": args_str},
        )

    def _parse_tool_result(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        result = str(obj.get("response", "") or obj.get("result", "") or obj.get("content", ""))
        yield AgentOutputLine(type="tool_result", content=result, raw=stripped, metadata=obj)

    def _parse_error(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        error_msg = extract_error_message(obj)
        yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)

    def _parse_message(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        parts = obj.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict):
                    part_dict = cast("JsonDict", part)
                    text = str(part_dict.get("text", ""))
                    if text:
                        if part_dict.get("thought") is True:
                            yield AgentOutputLine(type="thinking", content=text, raw=stripped)
                        else:
                            yield AgentOutputLine(type="text", content=text, raw=stripped)
                    function_call_obj = part_dict.get("function_call")
                    func: JsonDict | None = (
                        cast("JsonDict", function_call_obj)
                        if isinstance(function_call_obj, dict)
                        else None
                    )
                    if func:
                        tool_name = str(func.get("name", ""))
                        args_str = str(func.get("args", ""))
                        yield AgentOutputLine(
                            type="tool_use",
                            content=tool_name,
                            raw=stripped,
                            metadata={"tool": tool_name, "args": args_str},
                        )
        content = str(obj.get("content", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

    def _parse_candidates_response(self, obj: JsonDict, stripped: str) -> Iterator[AgentOutputLine]:
        candidates_val = obj.get("candidates")
        if not isinstance(candidates_val, list) or not candidates_val:
            return
        candidate = candidates_val[0]
        if not isinstance(candidate, dict):
            return
        content_val = cast("JsonDict", candidate).get("content")
        if not isinstance(content_val, dict):
            return
        parts_val = cast("JsonDict", content_val).get("parts")
        if not isinstance(parts_val, list):
            return
        for part in parts_val:
            if not isinstance(part, dict):
                continue
            part_dict = cast("JsonDict", part)
            text = str(part_dict.get("text", ""))
            if not text:
                continue
            if part_dict.get("thought") is True:
                yield AgentOutputLine(type="thinking", content=text, raw=stripped)
            else:
                yield AgentOutputLine(type="text", content=text, raw=stripped)

    def _extract_first_part_text(self, obj: JsonDict) -> str:
        parts_val: JsonValue | None = obj.get("parts")
        if isinstance(parts_val, list) and parts_val:
            first_part = parts_val[0]
            if isinstance(first_part, dict):
                part_dict = cast("JsonDict", first_part)
                return str(part_dict.get("text", ""))
        return ""


class GeminiParser(NdjsonParserBase):
    """Parser for Gemini's SSE+JSON streaming output with robust delta accumulation.

    Gemini uses SSE with data: lines containing JSON payloads.
    Each payload has a "type" field indicating what kind of content it carries.
    Text deltas are accumulated into coherent blocks before emission, flushing on:
    - ``done`` / ``stop`` / ``message_end`` (end of message)
    - ``\\n\\n`` paragraph boundary (incremental surfacing of long responses)
    - Iterator exhaustion (final flush via ``flush_accumulators()``)

    Inherits from :class:`NdjsonParserBase` which owns the
    ``data:`` strip, ``[DONE]`` short-circuit, JSON parse dispatch,
    lifecycle suppression, and error extraction.  The subclass
    ``_dispatch_json_object`` delegates to ``_GeminiDispatch`` for the
    per-event-type routing.
    """

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset({"done", "stop", "message_end"})

    def __init__(self) -> None:
        super().__init__()
        self._text_accumulator: TextAccumulator | None = None
        self._dispatcher = _GeminiDispatch(self)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._dispatcher.dispatch(obj, raw)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        if self._text_accumulator is None:
            return
        acc = self._text_accumulator
        self._text_accumulator = None
        yield from acc.flush(kind="text")
