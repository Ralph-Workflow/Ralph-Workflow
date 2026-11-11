
from __future__ import annotations

import json
import re
from json import JSONDecodeError
from typing import TYPE_CHECKING, ClassVar, cast

from .agent_output_line import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from .text_accumulator import TextAccumulator

__all__ = ["ParserTemplateBase"]

_EMITTABLE_TYPES: frozenset[str] = frozenset({"tool_use", "tool_result", "text", "thinking"})

_MAX_SUMMARY_LENGTH: int = 200
_MAX_PREFIX_LENGTH: int = 80



_CONTROL_CHARS_RE: re.Pattern[str] = re.compile(
    r"[\x00-\x1f\x7f]|\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07"
)

_SENSITIVE_JSON_KEYS: frozenset[str] = frozenset(
    {"arguments", "args", "file_path", "input", "prompt", "content"}
)

_SENSITIVE_PATH_TOKEN_RE: re.Pattern[str] = re.compile(
    r"""
    (?:/etc/|/proc/|/sys/|/root/|~/\.ssh/)[^\s\x1b\n]*
    |
    (?i:authorization)\s*:\s*(?i:bearer)[^\n]*
    |
    -----BEGIN\s+[A-Z ]*PRIVATE\s+KEY-----[^\n]*
    """,
    re.VERBOSE,
)

_SENSITIVE_MARKER_FALLBACK_RE: re.Pattern[str] = re.compile(
    r"""
    "(?i:arguments|args|file_path|input|prompt|content)"\s*:\s*"
    .*?
    (?=[,\}\]\n]|$)
    """,
    re.VERBOSE | re.DOTALL,
)


def _redact_json_values(obj: object) -> object:
    if isinstance(obj, dict):
        result: dict[str, object] = {}
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_JSON_KEYS:
                result[key] = "<redacted>"
            else:
                result[key] = _redact_json_values(value)
        return result
    if isinstance(obj, list):
        return [_redact_json_values(item) for item in obj]
    return obj


_JSON_DECODER = json.JSONDecoder(strict=False)


def _decode_json_at(text: str, pos: int) -> tuple[object, int]:
    try:
        decoded = cast("tuple[object, int]", _JSON_DECODER.raw_decode(text, pos))
    except (json.JSONDecodeError, ValueError):
        return None, -1
    value, end = decoded
    return value, end


def _redact_json_fragments(text: str) -> str:
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in {"{", "["}:
            parsed, end = _decode_json_at(text, i)
            if parsed is not None and isinstance(parsed, (dict, list)) and end > i:
                try:
                    redacted_obj = _redact_json_values(parsed)
                    redacted_text = json.dumps(redacted_obj, ensure_ascii=False)
                except (TypeError, ValueError):
                    out.append(text[i])
                    i += 1
                    continue
                out.append(redacted_text)
                i = end
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _sanitize_parser_subagent_summary(line: str) -> str:
    if not line:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub("", line)
    cleaned = _redact_json_fragments(cleaned)
    cleaned = _SENSITIVE_MARKER_FALLBACK_RE.sub("<redacted>", cleaned)
    cleaned = _SENSITIVE_PATH_TOKEN_RE.sub("<redacted>", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > _MAX_SUMMARY_LENGTH:
        cleaned = cleaned[:_MAX_SUMMARY_LENGTH]
    return cleaned


def _tool_name_from_line(line: AgentOutputLine) -> str:
    metadata = line.metadata or {}
    candidate = (
        metadata.get("tool")
        or metadata.get("tool_name")
        or metadata.get("toolName")
        or metadata.get("name")
    )
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return "unknown"


class ParserTemplateBase:

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset()

    _accumulators: dict[str, TextAccumulator]

    def __init__(self) -> None:
        self._accumulators: dict[str, TextAccumulator] = {}

    def emit_subagent_activity(
        self,
        line: AgentOutputLine,
        sink: Callable[[str], None],
    ) -> None:
        if line.type not in _EMITTABLE_TYPES:
            return
        tool_name = _tool_name_from_line(line)
        if line.type in {"tool_use", "tool_result"}:
            summary = f"{line.type}:{tool_name}"
        else:
            content = line.content.strip()
            truncated = (
                content[:_MAX_PREFIX_LENGTH] if len(content) > _MAX_PREFIX_LENGTH else content
            )
            summary = f"{line.type}:{truncated}"
        summary = _sanitize_parser_subagent_summary(summary)
        if not summary:
            return
        try:
            sink(summary)
        except Exception:
            return

    def parse_json_line(self, line: str) -> AgentOutputLine | None:
        stripped = line.strip()
        try:
            parsed: object = json.loads(stripped, strict=False)
        except JSONDecodeError:
            return AgentOutputLine(type="raw", content=stripped, raw=line)
        if not isinstance(parsed, dict):
            return AgentOutputLine(type="raw", content=stripped, raw=line)
        return self._classify_json_object(cast("dict[str, object]", parsed), line)

    def _classify_json_object(
        self,
        parsed: dict[str, object],
        raw: str,
    ) -> AgentOutputLine | None:
        return None

    def is_stop_event(self, event_type: str) -> bool:
        return event_type in self._STOP_EVENT_TYPES

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        for key in list(self._accumulators.keys()):
            acc = self._accumulators.pop(key)
            yield from acc.flush(kind="text")

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        result = self.parse_json_line(line)
        if result is not None:
            yield result

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for line in lines:
            yield from self.classify_line(line)
        yield from self.flush_accumulators()
