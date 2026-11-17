from __future__ import annotations

import json
import re
from contextlib import suppress
from json import JSONDecodeError
from typing import TYPE_CHECKING, ClassVar, cast

from ._object_dict import as_object_dict, as_object_list
from .agent_output_line import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from .text_accumulator import TextAccumulator

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
            if (
                parsed is not None
                and (any(isinstance(parsed, _t) for _t in (dict, list)))
                and end > i
            ):
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
    __slots__ = ("_accumulators",)
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
        with suppress(OSError, RuntimeError, ValueError, TypeError, AttributeError):
            sink(summary)

    def parse_json_line(self, line: str) -> AgentOutputLine | None:
        stripped = line.strip()
        try:
            parsed: object = json.loads(stripped, strict=False)
        except JSONDecodeError:
            return AgentOutputLine(type="raw", content=stripped, raw=line)
        if not isinstance(parsed, dict):
            return AgentOutputLine(type="raw", content=stripped, raw=line)
        return self._classify_json_object(as_object_dict(parsed), line)

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


import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ._event_classification import is_lifecycle_event
from .agent_output_line import channel_for_line_type
from .base import extract_error_message

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Literal

    from ralph.agents.idle_watchdog import SubagentPidRegistry

    _SubagentSourceLabel = Literal[
        "opencode",
        "claude",
        "pi",
        "agy",
        "generic",
        "claude_interactive",
        "codex",
        "nanocoder",
        "gemini",
        "cursor",
        "kimi",
    ]


def _extract_pid_from_obj(obj: dict[str, object]) -> int | None:
    for key in ("pid", "child_pid", "subagent_pid"):
        raw = obj.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float) and raw.is_integer():
            return int(raw)
    nested_meta = obj.get("metadata")
    if isinstance(nested_meta, dict):
        nested_pid = _extract_pid_from_obj(as_object_dict(nested_meta))
        if nested_pid is not None:
            return nested_pid
    part = obj.get("part")
    if isinstance(part, dict):
        state = as_object_dict(part).get("state")
        if isinstance(state, dict):
            state_pid = _extract_pid_from_obj(as_object_dict(state))
            if state_pid is not None:
                return state_pid
    return None


def _extract_source_timestamp(obj: dict[str, object]) -> str | None:
    for key in ("timestamp", "time", "ts"):
        raw = obj.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int | float):
            epoch = _epoch_to_iso(float(raw))
            if epoch is not None:
                return epoch
            continue
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = raw.strip()
        try:
            datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
        return candidate
    return None


_EPOCH_MILLISECONDS_BOUND = 1e11

_EPOCH_SECONDS_FLOOR = 946_684_800.0


def _epoch_to_iso(value: float) -> str | None:
    seconds = value / 1000.0 if abs(value) >= _EPOCH_MILLISECONDS_BOUND else value
    if seconds < _EPOCH_SECONDS_FLOOR:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


_SUBAGENT_SOURCE_BY_NAME: dict[str, _SubagentSourceLabel] = {
    "opencode": "opencode",
    "claude": "claude",
    "pi": "pi",
    "agy": "agy",
    "generic": "generic",
    "claude_interactive": "claude_interactive",
    "codex": "codex",
    "nanocoder": "nanocoder",
    "gemini": "gemini",
    "cursor": "cursor",
    "kimi": "kimi",
}


def _as_subagent_source_label(value: str) -> _SubagentSourceLabel:
    try:
        return _SUBAGENT_SOURCE_BY_NAME[value]
    except KeyError:
        raise ValueError(value) from None


class NdjsonParserBase(ParserTemplateBase):
    __slots__ = ("_subagent_pid_registry", "_subagent_source_label")

    def __init__(self) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = None
        self._subagent_source_label: str | None = None

    def _try_register_subagent_pid_from_obj(self, obj: dict[str, object]) -> None:
        registry = self._subagent_pid_registry
        if registry is None:
            return
        if self._subagent_source_label is None:
            return
        pid = _extract_pid_from_obj(obj)
        if pid is None or pid <= 0:
            return
        try:
            registry.register(
                pid,
                source=_as_subagent_source_label(self._subagent_source_label),
            )
        except ValueError:
            return

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = self._strip_data_prefix(line)
        if not stripped:
            return

        yield from self._classify_after_strip(stripped)

    def _strip_data_prefix(self, line: str) -> str:
        stripped = line.strip()
        if not stripped:
            return ""
        if stripped.startswith("data:"):
            return stripped[5:].strip()
        return stripped

    def _classify_after_strip(self, stripped: str) -> Iterator[AgentOutputLine]:
        if stripped == "[DONE]":
            yield AgentOutputLine(type="stop", raw=stripped)
            return

        try:
            parsed: object = json.loads(stripped, strict=False)
        except json.JSONDecodeError:
            yield from self._classify_non_json_line(stripped)
            return

        if not isinstance(parsed, dict):
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
            return

        yield from self._classify_parsed_json_object(as_object_dict(parsed), stripped)

    def _classify_parsed_json_object(
        self, obj: dict[str, object], stripped: str
    ) -> Iterator[AgentOutputLine]:
        source_timestamp = _extract_source_timestamp(obj)

        self._try_register_subagent_pid_from_obj(obj)

        if "error" in obj:
            yield from self._flush_before_error(obj, stripped)
            error_msg = extract_error_message(obj)
            yield AgentOutputLine(
                type="error",
                content=error_msg,
                raw=stripped,
                metadata=obj,
                timestamp=source_timestamp,
                channel=channel_for_line_type(str(obj.get("type", ""))),
            )
            return

        event_type = str(obj.get("type", ""))
        lifecycle_result: Iterator[AgentOutputLine] | None = None
        if event_type == "content_block_start" and isinstance(obj.get("content_block"), dict):
            pass
        elif event_type and is_lifecycle_event(event_type):
            lifecycle_result = self._handle_lifecycle_event(obj, event_type)

        yield from self._dispatch_with_timestamp(obj, stripped, source_timestamp, lifecycle_result)

    def _dispatch_with_timestamp(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None,
        lifecycle_result: Iterator[AgentOutputLine] | None,
    ) -> Iterator[AgentOutputLine]:
        if lifecycle_result is not None:
            yielded = lifecycle_result
        else:
            yielded = self._dispatch_json_object(obj, raw, source_timestamp)
        if source_timestamp is None:
            yield from yielded
            return
        for line in yielded:
            if isinstance(line, AgentOutputLine) and line.timestamp is None:
                yield replace(line, timestamp=source_timestamp)
            else:
                yield line

    def _flush_before_error(
        self,
        _obj: dict[str, object],
        _raw: str,
    ) -> Iterator[AgentOutputLine]:
        return iter(())

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        return iter(())

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))
        yield AgentOutputLine(
            type=event_type,
            raw=raw,
            metadata=obj,
            timestamp=source_timestamp,
        )

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type="raw", content=stripped, raw=stripped)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        return iter(())


import re
from typing import TYPE_CHECKING

from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from ralph.display.language_inference import lexer_for_path
from ralph.display.vt_normalizer import normalize_vt_text

from .content_channel import ContentChannel
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


_IDENTIFYING_PARAM_KEYS: tuple[str, ...] = ("TargetFile", "path", "file_path", "command", "query")
_PARAM_SUMMARY_MAX_LEN = 60


_FENCE_RE = re.compile(r"^[ \t]*```([\w.+-]*)[^\n]*\n", re.MULTILINE)


def _fence_language(content: str) -> str | None:
    match = _FENCE_RE.search(content)
    if match is None:
        return None
    info = str(match.group(1))
    if info:
        try:
            lexer = get_lexer_by_name(info)
        except (ClassNotFound, LookupError, ValueError, TypeError):
            return info.lower()
        return lexer.aliases[0] if lexer.aliases else info.lower()
    return lexer_for_path(None, content)


def _annotate_syntax_highlight(metadata: dict[str, object], content: str) -> None:
    language = _fence_language(content)
    if language is not None:
        metadata["syntax_highlight"] = True
        metadata["language"] = language


def _truncate(value: str) -> str:
    if len(value) <= _PARAM_SUMMARY_MAX_LEN:
        return value
    return value[: _PARAM_SUMMARY_MAX_LEN - 1] + "…"


def _basename_if_path(value: str) -> str:
    if "/" in value or "\\" in value:
        tail = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        return tail or value
    return value


def _parameter_summary(info: dict[str, object]) -> str | None:
    params = info.get("parameters")
    if not isinstance(params, dict):
        return None
    params_dict = as_object_dict(params)
    for key in _IDENTIFYING_PARAM_KEYS:
        value = params_dict.get(key)
        if isinstance(value, str) and value:
            return _truncate(_basename_if_path(value))
    for value in params_dict.values():
        if isinstance(value, str) and value:
            return _truncate(_basename_if_path(value))
    return None


def _format_duration(seconds: float) -> str:
    return f"{seconds:.2f}s"


def _completion_summary(label: str, step: dict[str, object], info: dict[str, object]) -> str:
    parts = [label]
    param_summary = _parameter_summary(info)
    if param_summary and param_summary not in label:
        parts.append(param_summary)
    duration = step.get("duration_seconds")
    if isinstance(duration, int | float) and not isinstance(duration, bool):
        parts.append(f"({_format_duration(duration)})")
    return " ".join(parts)


def _result_summary(res: dict[str, object]) -> str:
    parts = ["agy result"]
    status = res.get("status")
    if isinstance(status, str) and status:
        parts.append(status)
    detail_parts: list[str] = []
    duration = res.get("duration_seconds")
    if isinstance(duration, int | float) and not isinstance(duration, bool):
        detail_parts.append(_format_duration(duration))
    num_turns = res.get("num_turns")
    if isinstance(num_turns, int) and not isinstance(num_turns, bool):
        detail_parts.append(f"{num_turns} turn" + ("" if num_turns == 1 else "s"))
    if detail_parts:
        parts.append(f"({', '.join(detail_parts)})")
    return " ".join(parts)


def _merge_usage(older: dict[str, object], newer: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = dict(older)
    for key, new_value in newer.items():
        old_value = older.get(key)
        if (
            isinstance(new_value, int | float)
            and not isinstance(new_value, bool)
            and isinstance(old_value, int | float)
            and not isinstance(old_value, bool)
        ):
            merged[key] = old_value + new_value
        else:
            merged[key] = new_value
    return merged


def _tool_updates(
    step: dict[str, object],
) -> list[tuple[str, dict[str, object], str | None, bool]]:
    step_type = step.get("step_type")
    step_index = step.get("step_index")
    if step_type == "subagent":
        raw_info = step.get("subagent_info")
        if not isinstance(raw_info, dict):
            return []
        info = as_object_dict(raw_info)
        raw_subagents = info.get("subagents")
        if not isinstance(raw_subagents, list):
            return []
        subagents = as_object_list(raw_subagents)
        results: list[tuple[str, dict[str, object], str | None, bool]] = []
        for position, sub in enumerate(subagents):
            if not isinstance(sub, dict):
                continue
            details = as_object_dict(sub)
            cid = f"{step_index}:{position}" if step_index is not None else str(position)
            results.append(("subagent", details, cid, False))
        return results

    raw_info = step.get("tool_info")
    if not isinstance(raw_info, dict):
        return []
    info = as_object_dict(raw_info)
    tool_name_obj = info.get("name")
    tool_name = tool_name_obj if isinstance(tool_name_obj, str) and tool_name_obj else None
    if tool_name is None:
        raw_tool_name = step.get("tool_name")
        tool_name = raw_tool_name if isinstance(raw_tool_name, str) and raw_tool_name else None
    if step_type != "tool" or not tool_name:
        return []
    cid_obj = info.get("call_id") or info.get("id")
    id_is_synthesized = False
    if cid_obj is None and step_index is not None:
        cid_obj = step_index
        id_is_synthesized = True
    tool_cid: str | None = str(cid_obj) if cid_obj is not None else None
    return [(tool_name, info, tool_cid, id_is_synthesized)]


def _system_message_payload(frame: dict[str, object]) -> str | None:
    for key in ("text", "message", "content", "text_delta", "response"):
        value = frame.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_fallback_payload(obj: dict[str, object]) -> str | None:
    if not obj:
        return None
    data = obj.get("data")
    if isinstance(data, dict):
        msg = data.get("message") or data.get("text") or data.get("content")
        payload: object = msg if msg is not None else data
    elif data is not None:
        payload = data
    else:
        payload = next(
            (obj[k] for k in ("message", "text", "content", "error") if k in obj),
            None,
        )
        if payload is None:
            items = {k: v for k, v in obj.items() if k not in {"event", "type", "timestamp", "ts"}}
            payload = items if items else None
    return str(payload) if payload is not None else None


class AgyParser(NdjsonParserBase):
    __slots__ = (
        "_emitted_tool_use_ids",
        "_has_prior_text_line",
        "_pending_text_usage",
        "_text_accumulator",
    )

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: TextAccumulator | None = None
        self._has_prior_text_line: bool = False
        self._emitted_tool_use_ids: set[str] = set()
        self._pending_text_usage: dict[str, object] | None = None

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        normalized = normalize_vt_text(stripped).strip()
        if not normalized:
            return
        if self._text_accumulator is None:
            self._text_accumulator = TextAccumulator()
        chunk = f"\n{normalized}" if self._has_prior_text_line else normalized
        self._has_prior_text_line = True
        yield from self._text_accumulator.accumulate(
            chunk,
            stripped,
            kind="text",
            keep_current_when_empty=False,
        )

    def _flush_text(self) -> Iterator[AgentOutputLine]:
        if self._text_accumulator is None:
            return
        acc = self._text_accumulator
        self._text_accumulator = None
        self._has_prior_text_line = False
        pending_usage = self._pending_text_usage
        self._pending_text_usage = None
        for line in acc.flush(kind="text"):
            stripped_content = line.content.strip()
            if not stripped_content:
                continue
            metadata = dict(line.metadata) if line.metadata else {}
            if pending_usage is not None:
                metadata["usage"] = pending_usage
            _annotate_syntax_highlight(metadata, stripped_content)
            yield replace(line, content=stripped_content, metadata=metadata)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        event = obj.get("event")
        if event == "result":
            yield from self._dispatch_result_event(obj, raw)
        elif event == "step_update":
            update = obj.get("step_update")
            if isinstance(update, dict):
                yield from self._dispatch_step_update(as_object_dict(update), raw)
        elif event == "init":
            yield from self._dispatch_init_event(obj, raw)
        elif event == "error":
            payload = _extract_fallback_payload(obj)
            yield AgentOutputLine(type="error", content=payload or "", raw=raw, metadata=obj)
        elif event == "system_message":
            payload = _system_message_payload(obj) or _extract_fallback_payload(obj)
            if payload is not None:
                metadata: dict[str, object] = dict(obj)
                _annotate_syntax_highlight(metadata, payload)
                yield AgentOutputLine(type="text", content=payload, raw=raw, metadata=metadata)
        elif obj.get("type") == "tool_use":
            yield from self._dispatch_legacy_tool_use(obj, raw)
        else:
            payload = _extract_fallback_payload(obj)
            if payload:
                yield AgentOutputLine(type="text", content=payload, raw=raw, metadata=obj)

    def _dispatch_result_event(self, obj: dict[str, object], raw: str) -> Iterator[AgentOutputLine]:
        yield from self._flush_text()
        metadata: dict[str, object] = dict(obj)
        res = obj.get("result")
        if not isinstance(res, dict):
            yield AgentOutputLine(type="stop", content="agy result", raw=raw, metadata=metadata)
            return
        res_dict = as_object_dict(res)
        for key in ("status", "num_turns", "duration_seconds", "usage"):
            if key in res_dict:
                metadata[key] = res_dict[key]
        status = res_dict.get("status")
        metadata["_transcript_claimed_outcome"] = True
        if isinstance(status, str) and status != "SUCCESS":
            err_msg = str(res_dict.get("error") or status)
            yield AgentOutputLine(type="error", content=err_msg, raw=raw, metadata=metadata)
        yield AgentOutputLine(
            type="stop", content=_result_summary(res_dict), raw=raw, metadata=metadata
        )

    def _dispatch_init_event(self, obj: dict[str, object], raw: str) -> Iterator[AgentOutputLine]:
        init_info = obj.get("init")
        metadata: dict[str, object] = dict(obj)
        model: object = None
        if isinstance(init_info, dict):
            init_dict = as_object_dict(init_info)
            for key in ("model", "cwd", "tools", "permission_mode"):
                if key in init_dict:
                    metadata[key] = init_dict[key]
            model = init_dict.get("model")
        content = f"agy init {model}" if isinstance(model, str) and model else "agy init"
        yield AgentOutputLine(type="lifecycle", content=content, raw=raw, metadata=metadata)

    def _dispatch_legacy_tool_use(
        self, obj: dict[str, object], raw: str
    ) -> Iterator[AgentOutputLine]:
        tool_name = obj.get("name")
        if isinstance(tool_name, str) and tool_name:
            yield AgentOutputLine(
                type="tool_use", content=tool_name, raw=raw, metadata={"tool": tool_name, **obj}
            )

    def _dispatch_step_update(self, step: dict[str, object], raw: str) -> Iterator[AgentOutputLine]:
        text_delta = step.get("text_delta")
        if isinstance(text_delta, str) and text_delta:
            yield from self._accumulate_text_delta(text_delta, step, raw)
            return
        tool_updates = _tool_updates(step)
        if tool_updates:
            yield from self._flush_text()
            is_subagent = step.get("step_type") == "subagent"
            for tool_name, info, call_id, id_is_synthesized in tool_updates:
                yield from self._dispatch_tool_update(
                    step,
                    tool_name,
                    info,
                    call_id,
                    is_subagent=is_subagent,
                    id_is_synthesized=id_is_synthesized,
                    raw=raw,
                )
            return
        yield from self._dispatch_bodiless_step(step, raw)

    def _dispatch_bodiless_step(
        self, step: dict[str, object], raw: str
    ) -> Iterator[AgentOutputLine]:
        step_type = step.get("step_type")
        usage = step.get("usage")
        has_usage = isinstance(usage, dict) and bool(usage)
        metadata: dict[str, object] = dict(step)
        if has_usage:
            metadata["usage"] = usage
        label = step_type if isinstance(step_type, str) and step_type else "update"
        if step_type != "agent_response":
            if step_type == "system_message":
                payload = _system_message_payload(step)
                if payload is not None:
                    _annotate_syntax_highlight(metadata, payload)
                    yield AgentOutputLine(type="text", content=payload, raw=raw, metadata=metadata)
                    return
            yield AgentOutputLine(
                type="lifecycle", content=f"agy step {label}", raw=raw, metadata=metadata
            )
            return
        if not has_usage:
            return
        if self._text_accumulator is not None:
            usage_dict = as_object_dict(usage)
            if self._pending_text_usage is not None:
                self._pending_text_usage = _merge_usage(self._pending_text_usage, usage_dict)
            else:
                self._pending_text_usage = usage_dict
            return
        yield AgentOutputLine(
            type="lifecycle", content=f"agy step {label}", raw=raw, metadata=metadata
        )

    def _accumulate_text_delta(
        self, text_delta: str, step: dict[str, object], raw: str
    ) -> Iterator[AgentOutputLine]:
        normalized_delta = normalize_vt_text(text_delta)
        if not normalized_delta:
            return
        if self._text_accumulator is None:
            self._text_accumulator = TextAccumulator()
        usage = step.get("usage")
        if isinstance(usage, dict):
            self._pending_text_usage = as_object_dict(usage)
        yield from self._text_accumulator.accumulate(
            normalized_delta, raw, kind="text", keep_current_when_empty=False
        )

    def _tool_update_content_label(
        self,
        info: dict[str, object],
        tool_name: str,
        metadata: dict[str, object],
        *,
        is_subagent: bool,
    ) -> str:
        if is_subagent:
            for key in ("type_name", "role", "initial_prompt", "conversation_id", "log_uri"):
                value = info.get(key)
                if value is not None:
                    metadata[key] = value
            role = info.get("role")
            return str(role) if isinstance(role, str) and role else tool_name
        param_summary = _parameter_summary(info)
        return f"{tool_name} {param_summary}" if param_summary else tool_name

    def _dispatch_tool_update(
        self,
        step: dict[str, object],
        tool_name: str,
        info: dict[str, object],
        call_id: str | None,
        *,
        is_subagent: bool,
        raw: str,
        id_is_synthesized: bool = False,
    ) -> Iterator[AgentOutputLine]:
        metadata: dict[str, object] = {"tool": tool_name, "tool_info": info}
        parameters = info.get("parameters")
        if isinstance(parameters, dict):
            metadata["input"] = parameters
        if call_id:
            if id_is_synthesized:
                metadata["step_ordinal"] = call_id
            else:
                metadata["tool_use_id"] = call_id
        content_label = self._tool_update_content_label(
            info, tool_name, metadata, is_subagent=is_subagent
        )
        frame_channel = ContentChannel.SUBAGENT if is_subagent else ContentChannel.TOOL_CALL

        if step.get("state") == "DONE":
            if call_id is None or call_id not in self._emitted_tool_use_ids:
                if call_id:
                    self._emitted_tool_use_ids.add(call_id)
                yield AgentOutputLine(
                    type="tool_use",
                    content=content_label,
                    raw=raw,
                    metadata=metadata,
                    channel=frame_channel,
                )
            output = info.get("output")
            result_content = (
                output
                if isinstance(output, str) and output
                else _completion_summary(content_label, step, info)
            )
            yield AgentOutputLine(
                type="tool_result",
                content=result_content,
                raw=raw,
                metadata=metadata,
                channel=(ContentChannel.SUBAGENT if is_subagent else ContentChannel.TOOL_RESULT),
            )
            return
        if call_id:
            if call_id in self._emitted_tool_use_ids:
                return
            self._emitted_tool_use_ids.add(call_id)
        yield AgentOutputLine(
            type="tool_use",
            content=content_label,
            raw=raw,
            metadata=metadata,
            channel=frame_channel,
        )

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        yield from self._flush_text()


import json
import re
from typing import TYPE_CHECKING, Final

from ._assistant_envelope import (
    ENVELOPE_API_ERROR,
    ENVELOPE_SYNTHETIC,
    AssistantEnvelopeDecision,
    classify_assistant_record,
)
from .base import stringify_text_blocks

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry

_CLAUDE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^claude(?:/[^:\s]+)?(?=[ :]|$)")

_CLAUDE_TOP_LEVEL_LIFECYCLE: Final[frozenset[str]] = frozenset(
    {"message_start", "message_stop", "content_block_stop"}
)

_CLAUDE_NOISE_SYSTEM_SUBTYPES: Final[frozenset[str]] = frozenset(
    {"init", "hook_started", "hook_response", "thinking_tokens"}
)

_CLAUDE_EXPECTED_STOP_REASONS: Final[frozenset[str]] = frozenset({"end_turn", "tool_use"})

_CLAUDE_RATE_LIMIT_OK_STATUSES: Final[frozenset[str]] = frozenset({"allowed"})


def _content_block_fingerprint(block: dict[str, object]) -> str:
    return json.dumps(block, sort_keys=True, separators=(",", ":"), default=str)


class ClaudeParser(NdjsonParserBase):
    __slots__ = (
        "_current_message_id",
        "_emitted_text",
        "_fallback_accumulator",
        "_fallback_thinking_accumulator",
        "_seen_content_blocks",
        "_streamed_block_fingerprints",
        "_text_accumulator",
        "_thinking_accumulator",
    )

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: dict[tuple[str, int], TextAccumulator] = {}
        self._thinking_accumulator: dict[tuple[str, int], TextAccumulator] = {}
        self._fallback_accumulator: TextAccumulator | None = None
        self._fallback_thinking_accumulator: TextAccumulator | None = None
        self._current_message_id: str | None = None
        self._seen_content_blocks: set[tuple[str, int]] = set()
        self._emitted_text: set[str] = set()
        self._streamed_block_fingerprints: set[str] = set()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        if not stripped:
            return

        prefixed_lines = self._parse_prefixed_transcript_line(stripped)
        if prefixed_lines is not None:
            yield from prefixed_lines
            return

        yield from super().classify_line(stripped)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        for key in list(self._text_accumulator.keys()):
            yield from self._flush_text_accumulator(key)
        for key in list(self._thinking_accumulator.keys()):
            yield from self._flush_thinking_accumulator(key)
        yield from self._flush_fallback_accumulator()
        yield from self._flush_fallback_thinking_accumulator()

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        if event_type == "message_start":
            self._record_message_start(obj)
            return iter(())
        if event_type == "message_stop":
            flushed = self.flush_accumulators()
            self._current_message_id = None
            return flushed
        if event_type == "content_block_stop":
            return self._flush_content_block(obj)
        if event_type in _CLAUDE_TOP_LEVEL_LIFECYCLE:
            return iter(())
        return None

    def _record_message_start(self, obj: dict[str, object]) -> None:
        message = obj.get("message")
        if not isinstance(message, dict):
            return
        msg_id = str(message.get("id", ""))
        if msg_id:
            self._seen_content_blocks.clear()
            self._emitted_text.clear()
            self._streamed_block_fingerprints.clear()
            self._current_message_id = msg_id

    def _flush_content_block(self, obj: dict[str, object]) -> Iterator[AgentOutputLine]:
        index = obj.get("index")
        if isinstance(index, int) and self._current_message_id is not None:
            key = (self._current_message_id, index)
            if key in self._text_accumulator:
                yield from self._flush_text_accumulator(key)
            if key in self._thinking_accumulator:
                yield from self._flush_thinking_accumulator(key)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        event_type = str(obj.get("type", "unknown"))

        self._try_register_subagent_pid_from_obj(obj)

        if event_type == "stream_event":
            event = obj.get("event")
            if isinstance(event, dict):
                yield from self._parse_stream_inner(event, raw)
            else:
                yield AgentOutputLine(type="stream_event", raw=raw, metadata=obj)
        elif event_type == "content_block_delta":
            yield from self._parse_content_block_delta(obj, raw)
        elif event_type == "content_block_start":
            self._track_content_block_start(obj)
            yield from self._parse_content_block_start(obj, raw)
        elif event_type in ("assistant", "user"):
            yield from self._parse_role_message(obj, raw)
        elif event_type == "result":
            yield from self._parse_result_event(obj, raw)
        elif event_type == "error":
            yield from self._parse_error_event(obj, raw)
        elif event_type == "system":
            yield from self._parse_system_event(obj, raw)
        elif event_type == "rate_limit_event":
            yield from self._parse_rate_limit_event(obj, raw)
        else:
            yield self._unclassified_event_line(event_type, obj, raw)

    def _parse_system_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        subtype = str(obj.get("subtype", ""))
        if subtype in _CLAUDE_NOISE_SYSTEM_SUBTYPES:
            return
        status = obj.get("status")
        if isinstance(status, str) and status:
            content = f"{subtype} ({status})" if subtype else status
        else:
            content = subtype or "unknown"
        yield AgentOutputLine(type="system", content=content, raw=raw, metadata=obj)

    def _parse_rate_limit_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        info = obj.get("rate_limit_info")
        info_dict: dict[str, object] = info if isinstance(info, dict) else {}
        status = str(info_dict.get("status", ""))
        using_overage = bool(info_dict.get("isUsingOverage", False))
        if status in _CLAUDE_RATE_LIMIT_OK_STATUSES and not using_overage:
            return
        yield AgentOutputLine(
            type="rate_limit_event",
            content=status or "unknown",
            raw=raw,
            metadata=obj,
        )

    def _unclassified_event_line(
        self,
        event_type: str,
        obj: dict[str, object],
        raw: str,
    ) -> AgentOutputLine:
        subtype = obj.get("subtype")
        content = str(subtype) if subtype else ""
        return AgentOutputLine(type=event_type, content=content, raw=raw, metadata=obj)

    def _track_content_block_start(self, obj: dict[str, object]) -> None:
        content_block = obj.get("content_block")
        if not isinstance(content_block, dict) or self._current_message_id is None:
            return
        index = obj.get("index")
        if not isinstance(index, int):
            return
        block_type = str(content_block.get("type", ""))
        key = (self._current_message_id, index)
        self._seen_content_blocks.add(key)
        if block_type == "text":
            if key not in self._text_accumulator:
                self._text_accumulator[key] = TextAccumulator()
        elif block_type == "thinking" and key not in self._thinking_accumulator:
            self._thinking_accumulator[key] = TextAccumulator()

    def _parse_stream_inner(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(event.get("type", "unknown"))

        if event_type == "message_start":
            self._record_message_start(event)
        elif event_type == "message_stop":
            yield from self.flush_accumulators()
            self._current_message_id = None
        elif event_type == "content_block_stop":
            yield from self._flush_content_block(event)
        elif event_type == "content_block_delta":
            yield from self._parse_content_block_delta(event, raw)
        elif event_type == "content_block_start":
            self._track_content_block_start(event)
            yield from self._parse_stream_content_block_start(event, raw)
        elif event_type == "error":
            yield from self._parse_stream_error(event, raw)
        elif event_type == "message_delta":
            yield from self._parse_stream_message_delta(event, raw)
        elif event_type not in _CLAUDE_TOP_LEVEL_LIFECYCLE:
            yield self._unclassified_event_line(event_type, event, raw)

    def _parse_stream_message_delta(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        delta = event.get("delta")
        delta_dict: dict[str, object] = delta if isinstance(delta, dict) else {}
        stop_reason = delta_dict.get("stop_reason")
        if stop_reason is None or str(stop_reason) in _CLAUDE_EXPECTED_STOP_REASONS:
            return
        yield AgentOutputLine(
            type="message_delta", content=str(stop_reason), raw=raw, metadata=event
        )

    def _parse_content_block_delta(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        delta = obj.get("delta")
        if not isinstance(delta, dict):
            return

        delta_type = str(delta.get("type", "text_delta" if "text" in delta else ""))

        if delta_type == "thinking_delta":
            yield from self._accumulate_thinking_delta(obj, delta, raw)
            return

        if delta_type != "text_delta":
            return

        text = str(delta.get("text", ""))
        if not text:
            return

        index = obj.get("index")
        block_key: tuple[str, int] | None = None

        if isinstance(index, int) and self._current_message_id is not None:
            block_key = (self._current_message_id, index)
            if block_key in self._text_accumulator:
                yield from self._text_accumulator[block_key].accumulate(
                    text, raw, kind="text", keep_current_when_empty=False
                )
                return

        if self._fallback_accumulator is None:
            self._fallback_accumulator = TextAccumulator()
        yield from self._fallback_accumulator.accumulate(
            text, raw, kind="text", keep_current_when_empty=True
        )

    def _accumulate_thinking_delta(
        self,
        obj: dict[str, object],
        delta: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        text = str(delta.get("thinking", delta.get("text", "")))
        if not text.strip() and "\n\n" not in text:
            return

        index = obj.get("index")

        if isinstance(index, int) and self._current_message_id is not None:
            key = (self._current_message_id, index)
            if key in self._thinking_accumulator:
                yield from self._thinking_accumulator[key].accumulate(
                    text, raw, kind="thinking", keep_current_when_empty=False
                )
                return

        if self._fallback_thinking_accumulator is None:
            self._fallback_thinking_accumulator = TextAccumulator()
        yield from self._fallback_thinking_accumulator.accumulate(
            text, raw, kind="thinking", keep_current_when_empty=True
        )

    def _flush_text_accumulator(self, key: tuple[str, int]) -> Iterator[AgentOutputLine]:
        if key not in self._text_accumulator:
            return
        acc = self._text_accumulator.pop(key)
        for line in acc.flush(kind="text"):
            self._emitted_text.add(line.content)
            yield line

    def _flush_thinking_accumulator(self, key: tuple[str, int]) -> Iterator[AgentOutputLine]:
        if key not in self._thinking_accumulator:
            return
        acc = self._thinking_accumulator.pop(key)
        yield from acc.flush(kind="thinking", require_strip=True)

    def _flush_fallback_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._fallback_accumulator is None:
            return
        acc = self._fallback_accumulator
        self._fallback_accumulator = None
        for line in acc.flush(kind="text"):
            self._emitted_text.add(line.content)
            yield line

    def _flush_fallback_thinking_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._fallback_thinking_accumulator is None:
            return
        acc = self._fallback_thinking_accumulator
        self._fallback_thinking_accumulator = None
        yield from acc.flush(kind="thinking", require_strip=True)

    def _parse_result_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        subtype = str(obj.get("subtype", ""))
        if subtype == "error":
            error = str(obj.get("error", "unknown error"))
            yield AgentOutputLine(type="error", content=error, raw=raw, metadata=obj)
            return

        result = str(obj.get("result", ""))
        if result and result not in self._emitted_text:
            yield AgentOutputLine(type="text", content=result, raw=raw, metadata=obj)

    def _parse_content_block_start(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        content_block = obj.get("content_block")
        if not isinstance(content_block, dict):
            return

        yield from self._parse_content_block(content_block, raw)

    def _parse_error_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        error_msg = extract_error_message(obj)
        yield AgentOutputLine(type="error", content=error_msg, raw=raw, metadata=obj)

    def _parse_stream_content_block_start(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        content_block = event.get("content_block")
        if not isinstance(content_block, dict):
            return

        self._streamed_block_fingerprints.add(_content_block_fingerprint(content_block))
        yield from self._parse_content_block(content_block, raw)

    def _parse_stream_error(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        error = event.get("error")
        if isinstance(error, dict):
            error_msg = str(error.get("message", error.get("code", "unknown error")))
        else:
            error_msg = "unknown error"
        yield AgentOutputLine(type="error", content=error_msg, raw=raw, metadata=event)

    def _parse_role_message(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        if obj.get("type") == "assistant":
            decision: AssistantEnvelopeDecision = classify_assistant_record(obj)
            if decision.envelope == ENVELOPE_API_ERROR:
                if decision.error_text:
                    yield AgentOutputLine(
                        type="error",
                        content=decision.error_text,
                        raw=raw,
                        metadata=obj,
                    )
                return
            if decision.envelope == ENVELOPE_SYNTHETIC:
                payload = decision.synthetic_text or "synthetic bookkeeping turn"
                yield AgentOutputLine(
                    type="lifecycle",
                    content=payload,
                    raw=raw,
                    metadata=obj,
                )
                return

        message = obj.get("message")
        if not isinstance(message, dict):
            return

        content = message.get("content")
        if not isinstance(content, list):
            return

        message_id = str(message.get("id", ""))
        yield from self._parse_message_content(content, raw, message_id)

    def _parse_message_content(
        self,
        content: list[object],
        raw: str,
        message_id: str = "",
    ) -> Iterator[AgentOutputLine]:
        for index, block in enumerate(content):
            if message_id and (message_id, index) in self._seen_content_blocks:
                continue
            if not isinstance(block, dict):
                continue

            block_obj = as_object_dict(block)
            if _content_block_fingerprint(block_obj) in self._streamed_block_fingerprints:
                continue
            block_type = str(block_obj.get("type", ""))
            if block_type == "text":
                text = str(block_obj.get("text", ""))
                if text:
                    yield AgentOutputLine(type="text", content=text, raw=raw, metadata=block_obj)
                continue

            if block_type == "tool_use":
                tool_name = str(block_obj.get("name", "unknown"))
                yield AgentOutputLine(
                    type="tool_use", content=tool_name, raw=raw, metadata=block_obj
                )
                continue

            if block_type == "tool_result":
                yield from self._parse_tool_result(block_obj, raw)
                continue

            if block_type == "thinking":
                text = str(block_obj.get("thinking", block_obj.get("text", "")))
                if text.strip():
                    yield AgentOutputLine(
                        type="thinking", content=text, raw=raw, metadata=block_obj
                    )
                continue

            yield AgentOutputLine(
                type="error",
                content=f"unsupported content block type '{block_type}' in agent output",
                raw=raw,
                metadata=block_obj,
            )

    def _parse_plain_text_prefix(self, raw: str, text: str) -> list[AgentOutputLine]:
        if is_lifecycle_event(text) or text.startswith("system (status="):
            return []
        return [AgentOutputLine(type="text", content=text, raw=raw)]

    def _parse_structured_remainder(self, raw: str, remainder: str) -> list[AgentOutputLine] | None:
        for role in ("user", "assistant"):
            role_prefix = f" {role}: message="
            if remainder.startswith(role_prefix):
                return self._parse_prefixed_message_line(raw, remainder[len(role_prefix) :])
        if remainder.startswith(" message_delta") or remainder.startswith(" system: status="):
            return []
        if remainder.startswith(" ✗: "):
            return [AgentOutputLine(type="error", content=remainder[4:], raw=raw)]
        return None

    def _parse_prefixed_transcript_line(self, raw: str) -> list[AgentOutputLine] | None:
        if raw.startswith("[claude]:"):
            return []
        m = _CLAUDE_PREFIX_RE.match(raw)
        if m is None:
            return None
        remainder = raw[m.end() :]
        if remainder.startswith(": "):
            return self._parse_plain_text_prefix(raw, remainder[2:])
        if remainder.startswith(" tool: "):
            return self._parse_prefixed_tool_line(raw, remainder[7:])
        return self._parse_structured_remainder(raw, remainder)

    def _parse_prefixed_tool_line(self, raw: str, payload: str) -> list[AgentOutputLine]:
        payload = payload.strip()
        tool_name, has_details, detail_suffix = payload.partition(" (")
        metadata: dict[str, object] = {}
        if has_details and detail_suffix.endswith(")"):
            metadata["input"] = {"args": detail_suffix[:-1]}
        return [
            AgentOutputLine(
                type="tool_use",
                content=tool_name.strip() or "unknown",
                raw=raw,
                metadata=metadata,
            )
        ]

    def _parse_prefixed_message_line(
        self, raw: str, json_payload: str
    ) -> list[AgentOutputLine] | None:
        try:
            parsed: object = json.loads(json_payload)
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, dict):
            return None

        content = parsed.get("content")
        if not isinstance(content, list):
            return []

        return list(self._parse_message_content(content, raw))

    def _parse_content_block(
        self,
        content_block: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        block_type = str(content_block.get("type", "unknown"))

        if block_type == "text":
            text = str(content_block.get("text", ""))
            if text:
                yield AgentOutputLine(type="text", content=text, raw=raw, metadata=content_block)
            return

        if block_type == "tool_use":
            tool_name = str(content_block.get("name", "unknown"))
            yield AgentOutputLine(
                type="tool_use", content=tool_name, raw=raw, metadata=content_block
            )
            return

        if block_type == "tool_result":
            yield from self._parse_tool_result(content_block, raw)
            return

        if block_type == "thinking":
            return

        yield AgentOutputLine(
            type="error",
            content=f"unsupported content block type '{block_type}' in agent output",
            raw=raw,
            metadata=content_block,
        )

    def _parse_tool_result(
        self,
        block: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        result_type = "error" if block.get("is_error") else "tool_result"
        content = block.get("content")
        if content is None:
            yield AgentOutputLine(
                type=result_type,
                content="",
                raw=raw,
                metadata=block,
                channel=ContentChannel.TOOL_RESULT,
            )
            return

        if isinstance(content, list):
            tool_result = stringify_text_blocks(content, require_text_type=True)
            yield AgentOutputLine(
                type=result_type,
                content=tool_result,
                raw=raw,
                metadata=block,
                channel=ContentChannel.TOOL_RESULT,
            )
            return

        yield AgentOutputLine(
            type=result_type,
            content=str(content),
            raw=raw,
            metadata=block,
            channel=ContentChannel.TOOL_RESULT,
        )


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


def _parse_codex_object(
    obj: dict[str, object],
    stripped: str,
) -> Iterator[AgentOutputLine]:
    yield from _CodexDispatch().dispatch(obj, stripped)


def _codex_delta_content(delta_val: object) -> str:
    if isinstance(delta_val, dict):
        delta_obj = as_object_dict(delta_val)
        content_val = delta_obj.get("content") or delta_obj.get("text")
        return str(content_val or "")
    if isinstance(delta_val, str):
        return delta_val
    return ""


class _CodexDispatch:
    __slots__ = (
        "_accumulators",
        "_current_response_id",
        "_owner",
        "_stream_counter",
    )

    def __init__(self, owner: CodexParser | None = None) -> None:
        self._owner = owner
        self._accumulators: dict[str, TextAccumulator] = {}
        self._current_response_id: str | None = None
        self._stream_counter = 0

    def dispatch(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if event_type in CodexParser._STOP_EVENT_TYPES:
            yield from self._flush_all()
            self._current_response_id = None
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

        handler_map = {
            "text": self._parse_text_content,
            "content": self._parse_text_content,
            "text_delta": self._parse_text_delta,
            "response.output_text": self._parse_text_content,
            "response.output_text.delta": self._parse_text_delta,
            "tool_use": self._parse_tool_use,
            "tool_result": self._parse_tool_result,
            "tool_result_delta": self._parse_tool_result,
            "error": self._parse_error,
            "error_delta": self._parse_error,
            "assistant": self._parse_assistant,
            "item.started": self._parse_item_event,
            "item.completed": self._parse_item_event,
            "result": self._parse_result,
            "turn.failed": self._parse_turn_failed,
        }

        handler = handler_map.get(event_type)
        if handler:
            yield from handler(obj, stripped)
            return

        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _flush_all(self) -> Iterator[AgentOutputLine]:
        for key in list(self._accumulators.keys()):
            yield from self._flush_accumulator(key)

    def _parse_text_content(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        content = str(obj.get("content", "") or obj.get("text", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)

    def _parse_text_delta(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        delta_val = obj.get("delta")
        content = _codex_delta_content(delta_val)

        if not content:
            return

        response_id = str(obj.get("response_id", obj.get("responseId", "")) or "")
        if not response_id:
            if self._current_response_id:
                response_id = self._current_response_id
            else:
                yield AgentOutputLine(type="text", content=content, raw=stripped)
                return

        key = response_id
        accumulators = self._accumulators
        if self._owner is not None:
            accumulators = self._owner._accumulators
        if key not in accumulators:
            accumulators[key] = TextAccumulator()
        yield from accumulators[key].accumulate(
            content, stripped, kind="text", keep_current_when_empty=True
        )

    def _flush_accumulator(self, key: str) -> Iterator[AgentOutputLine]:
        accumulators = self._accumulators
        if self._owner is not None:
            accumulators = self._owner._accumulators
        if key not in accumulators:
            return
        acc = accumulators.pop(key)
        yield from acc.flush(kind="text")

    def _parse_tool_use(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        tool_name = str(obj.get("tool", obj.get("name", "unknown")))
        tool_input = obj.get("input", {})
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "input": tool_input},
        )

    def _parse_tool_result(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        result = str(obj.get("result", obj.get("content", obj.get("output", ""))))
        yield AgentOutputLine(type="tool_result", content=result, raw=stripped, metadata=obj)

    def _parse_error(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        error_msg = extract_error_message(obj)
        yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)

    def _parse_turn_failed(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        error_message = str(obj.get("error", "turn failed"))
        yield AgentOutputLine(type="error", content=error_message, raw=stripped, metadata=obj)

    def _parse_assistant(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        content = str(obj.get("content", ""))
        if content:
            yield AgentOutputLine(type="text", content=content, raw=stripped)
        yield AgentOutputLine(type="assistant", raw=stripped, metadata=obj)

    def _parse_result(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        result = str(obj.get("result", ""))
        if result:
            yield AgentOutputLine(type="text", content=result, raw=stripped, metadata=obj)

    def _parse_item_event(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        item_obj = obj.get("item")
        if not isinstance(item_obj, dict):
            yield AgentOutputLine(type=str(obj.get("type", "item")), raw=stripped, metadata=obj)
            return

        item_type = str(item_obj.get("type", "unknown"))
        text = str(item_obj.get("text", ""))

        if item_type == "reasoning" and text:
            yield AgentOutputLine(type="thinking", content=text, raw=stripped, metadata=item_obj)
            return

        if item_type == "agent_message" and text:
            yield AgentOutputLine(type="text", content=text, raw=stripped, metadata=item_obj)
            return

        if item_type == "mcp_tool_call":
            tool_name = str(item_obj.get("tool", "unknown"))
            arguments: object = item_obj.get("arguments", {})
            call_id = str(item_obj.get("id", ""))
            metadata = {
                "tool": tool_name,
                "input": arguments,
                "tool_call_id": call_id,
            }
            if str(obj.get("type", "")) == "item.completed":
                result = item_obj.get("result", item_obj.get("error", ""))
                yield AgentOutputLine(
                    type="tool_result",
                    content=_item_result_content(result),
                    raw=stripped,
                    metadata={
                        **metadata,
                        "result": result,
                        "error": item_obj.get("error", ""),
                    },
                )
            else:
                yield AgentOutputLine(
                    type="tool_use", content=tool_name, raw=stripped, metadata=metadata
                )
            return

        if item_type == "command_execution":
            command = str(item_obj.get("command", ""))
            if command:
                metadata = {
                    "tool": "bash",
                    "command": command,
                    "tool_call_id": str(item_obj.get("id", "")),
                }
                if str(obj.get("type", "")) == "item.completed":
                    result = item_obj.get("result", item_obj.get("error", ""))
                    yield AgentOutputLine(
                        type="tool_result",
                        content=_item_result_content(result),
                        raw=stripped,
                        metadata={
                            **metadata,
                            "result": result,
                            "exit_code": item_obj.get("exit_code", 0),
                            "error": item_obj.get("error", ""),
                        },
                    )
                else:
                    yield AgentOutputLine(
                        type="tool_use", content="bash", raw=stripped, metadata=metadata
                    )
            else:
                yield AgentOutputLine(
                    type="item_command_execution",
                    raw=stripped,
                    metadata=item_obj,
                )
            return

        if item_type in {"mcp_tool_result", "tool_result", "mcp_result"}:
            tool_name = str(item_obj.get("tool", "unknown"))
            result_obj = item_obj.get("result", item_obj.get("output", item_obj.get("content", "")))
            content = result_obj if isinstance(result_obj, str) else ""
            yield AgentOutputLine(
                type="tool_result",
                content=content,
                raw=stripped,
                metadata={"tool": tool_name, "result": result_obj},
            )
            return

        yield AgentOutputLine(type=f"item_{item_type}", raw=stripped, metadata=item_obj)


def _item_result_content(result: object) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            texts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("text")
            ]
            if texts:
                return " ".join(texts)
    return ""


class CodexParser(NdjsonParserBase):
    __slots__ = ("_current_response_id", "_dispatcher", "_stream_counter")
    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset(
        {"turn.completed", "message_stop", "done", "stop", "response.completed"}
    )

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._accumulators: dict[str, TextAccumulator] = {}
        self._current_response_id: str | None = None
        self._stream_counter = 0
        self._dispatcher = _CodexDispatch(self)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        yield from self._dispatcher.dispatch(obj, raw)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        for key in list(self._accumulators.keys()):
            if key not in self._accumulators:
                continue
            acc = self._accumulators.pop(key)
            yield from acc.flush(kind="text")


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


_TEXT_KIND = "text"
_THINKING_KIND = "thinking"
_CURSOR_TOOL_CALL_METADATA_KEYS = frozenset(
    {
        "toolCallId",
        "startedAtMs",
        "completedAtMs",
        "hookAdditionalContexts",
    }
)
_CURSOR_MCP_WRAPPER_TOOL_NAME = "mcpToolCall"
_CURSOR_RALPH_MCP_NAME_PREFIX = "ralph-mcp__ralph__"
_RALPH_MCP_NAME_PREFIX = "mcp__ralph__"

_HANDLER_RETURNS_NO_EVENTS = False


def _cursor_nested_tool_call(
    obj: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    tool_call = obj.get("tool_call")
    if not isinstance(tool_call, dict):
        return None
    tool_call_dict = as_object_dict(tool_call)
    for key, value in tool_call_dict.items():
        if key in _CURSOR_TOOL_CALL_METADATA_KEYS:
            continue
        if not isinstance(value, dict):
            continue
        return key, as_object_dict(value)
    return None


def _cursor_tool_name_and_args(obj: dict[str, object]) -> tuple[str, dict[str, object]]:
    nested = _cursor_nested_tool_call(obj)
    if nested is not None:
        tool_name, payload = nested
        args = payload.get("args", payload.get("input", {}))
        if not isinstance(args, dict):
            args = {}
        if tool_name == _CURSOR_MCP_WRAPPER_TOOL_NAME:
            return _cursor_mcp_tool_name_and_args(as_object_dict(args))
        return tool_name, as_object_dict(args)

    tool_name = str(obj.get("toolName", obj.get("name", "unknown")))
    args = obj.get("args", obj.get("input", {}))
    if not isinstance(args, dict):
        args = {}
    return tool_name, as_object_dict(args)


def _cursor_mcp_tool_name_and_args(args: dict[str, object]) -> tuple[str, dict[str, object]]:
    raw_name = args.get("toolName")
    if not isinstance(raw_name, str) or not raw_name:
        raw_name = args.get("name")
    tool_name = (
        raw_name if isinstance(raw_name, str) and raw_name else _CURSOR_MCP_WRAPPER_TOOL_NAME
    )
    if tool_name.startswith(_CURSOR_RALPH_MCP_NAME_PREFIX):
        tool_name = _RALPH_MCP_NAME_PREFIX + tool_name[len(_CURSOR_RALPH_MCP_NAME_PREFIX) :]

    inner_args = args.get("args", args.get("input", {}))
    if not isinstance(inner_args, dict):
        inner_args = {}
    return tool_name, as_object_dict(inner_args)


def _cursor_tool_result_payload(obj: dict[str, object]) -> object:
    nested = _cursor_nested_tool_call(obj)
    if nested is not None:
        _tool_name, payload = nested
        return payload.get("result", payload.get("output", payload.get("content", "")))
    return obj.get("result", obj.get("output", obj.get("content", "")))


def _string_field(obj: dict[str, object], key: str) -> str | None:
    value = obj.get(key)
    return value if isinstance(value, str) else None


def _cursor_tool_result_text(result: object) -> str:
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result) if result else ""
    result_dict = as_object_dict(result)
    success = result_dict.get("success")
    if isinstance(success, dict):
        success_dict = as_object_dict(success)
        return (
            _cursor_content_text(success_dict.get("content"))
            or _string_field(success_dict, "text")
            or _string_field(success_dict, "message")
            or _string_field(success_dict, "path")
            or str(result)
        )
    error = result_dict.get("error")
    if isinstance(error, dict):
        error_dict = as_object_dict(error)
        return _string_field(error_dict, "message") or str(result)
    if isinstance(error, str):
        return error
    return _string_field(result_dict, "message") or str(result)


def _cursor_content_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    for item in content:
        item_text = _cursor_content_item_text(item)
        if item_text:
            return item_text
    return None


def _cursor_content_item_text(item: object) -> str | None:
    if isinstance(item, str):
        return item or None
    if not isinstance(item, dict):
        return None
    item_dict = as_object_dict(item)
    text = item_dict.get("text")
    if isinstance(text, str) and text:
        return text
    if isinstance(text, dict):
        nested_text = as_object_dict(text).get("text")
        if isinstance(nested_text, str) and nested_text:
            return nested_text
    content_value = item_dict.get("content")
    return content_value if isinstance(content_value, str) and content_value else None


def _cursor_tool_result_is_error(obj: dict[str, object]) -> bool:
    is_error = obj.get("is_error", obj.get("isError", False))
    if isinstance(is_error, str):
        return is_error.casefold() in {"true", "1", "yes"}
    if bool(is_error):
        return True
    nested = _cursor_nested_tool_call(obj)
    if nested is None:
        return "error" in obj
    _tool_name, payload = nested
    result = payload.get("result")
    return isinstance(result, dict) and "error" in result


class _CursorDispatch:
    __slots__ = ("_owner",)

    def __init__(self, owner: CursorParser) -> None:
        self._owner = owner

    def dispatch(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        handler_map = {
            "system": self._handle_system,
            "user": self._handle_user,
            "assistant": self._handle_assistant,
            "thinking": self._handle_thinking,
            "tool_call": self._handle_tool_call,
            "tool_result": self._handle_tool_result,
            "result": self._handle_result,
        }

        handler = handler_map.get(event_type)
        if handler is not None:
            yield from handler(obj, stripped)
            return

        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _handle_system(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        message = obj.get("message") or obj.get("content") or ""
        if not isinstance(message, str):
            message = str(message) if message else ""
        if not message and obj.get("subtype") == "init":
            session_id = obj.get("session_id")
            model = obj.get("model")
            if isinstance(session_id, str) and isinstance(model, str):
                message = f"cursor session {session_id} initialized with model {model}"
        if not message:
            return
        yield AgentOutputLine(type="status", content=message, raw=stripped, metadata=obj)

    def _handle_user(
        self,
        _obj: dict[str, object],
        _stripped: str,
    ) -> Iterator[AgentOutputLine]:
        if _HANDLER_RETURNS_NO_EVENTS:
            yield from ()
        return

    def _handle_assistant(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        message = obj.get("message")
        if not isinstance(message, dict):
            return
        message_dict = as_object_dict(message)
        content = message_dict.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            block_dict = as_object_dict(block)
            block_type = str(block_dict.get("type", ""))
            if block_type == _TEXT_KIND:
                text = str(block_dict.get("text", ""))
                if not text:
                    continue
                yield from self._owner._text_accumulator.accumulate(
                    text, stripped, kind=_TEXT_KIND, keep_current_when_empty=False
                )
                continue
            if block_type == _THINKING_KIND:
                thinking = str(block_dict.get("thinking", block_dict.get("text", "")))
                if not thinking.strip():
                    continue
                yield from self._owner._thinking_accumulator.accumulate(
                    thinking,
                    stripped,
                    kind=_THINKING_KIND,
                    keep_current_when_empty=False,
                )
                continue
            if block_type == "tool_call":
                yield from self._owner.flush_accumulators()
                tool_name, args = _cursor_tool_name_and_args(block_dict)
                yield AgentOutputLine(
                    type="tool_use",
                    content=tool_name,
                    raw=stripped,
                    metadata={"tool": tool_name, "args": args, **block_dict},
                )

    def _handle_thinking(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        text = str(obj.get("text", obj.get("thinking", "")))
        if not text.strip():
            return
        yield from self._owner._thinking_accumulator.accumulate(
            text,
            stripped,
            kind=_THINKING_KIND,
            keep_current_when_empty=False,
        )

    def _handle_tool_call(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._owner.flush_accumulators()
        if obj.get("subtype") == "completed":
            yield from self._handle_tool_result(obj, stripped)
            return
        tool_name, args = _cursor_tool_name_and_args(obj)
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "args": args, **obj},
        )

    def _handle_tool_result(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        if _cursor_tool_result_is_error(obj):
            error_msg = extract_error_message(obj)
            yield AgentOutputLine(
                type="error",
                content=error_msg,
                raw=stripped,
                metadata=obj,
                channel=ContentChannel.TOOL_RESULT,
            )
            return
        result = _cursor_tool_result_text(_cursor_tool_result_payload(obj))
        tool_name, _args = _cursor_tool_name_and_args(obj)
        yield AgentOutputLine(
            type="tool_result",
            content=result,
            raw=stripped,
            metadata={"tool": tool_name, **obj},
        )

    def _handle_result(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._owner.flush_accumulators()
        yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)


class CursorParser(NdjsonParserBase):
    __slots__ = ("_dispatcher", "_text_accumulator", "_thinking_accumulator")
    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset({"result"})

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: TextAccumulator = TextAccumulator()
        self._thinking_accumulator: TextAccumulator = TextAccumulator()
        self._dispatcher = _CursorDispatch(self)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        yield from self._dispatcher.dispatch(obj, raw)

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        return None

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        yield from self._text_accumulator.flush(kind=_TEXT_KIND)
        yield from self._thinking_accumulator.flush(kind=_THINKING_KIND, require_strip=True)


from typing import TYPE_CHECKING

JsonValue = object
JsonDict = dict[str, JsonValue]

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


class _GeminiDispatch:
    __slots__ = ("_owner",)

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
            as_object_dict(function_call_obj) if isinstance(function_call_obj, dict) else None
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
                args_str = str(as_object_dict(func_args))
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
                    part_dict = as_object_dict(part)
                    text = str(part_dict.get("text", ""))
                    if text:
                        if part_dict.get("thought") is True:
                            yield AgentOutputLine(type="thinking", content=text, raw=stripped)
                        else:
                            yield AgentOutputLine(type="text", content=text, raw=stripped)
                    function_call_obj = part_dict.get("function_call")
                    func: JsonDict | None = (
                        as_object_dict(function_call_obj)
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
        content_val = as_object_dict(candidate).get("content")
        if not isinstance(content_val, dict):
            return
        parts_val = as_object_dict(content_val).get("parts")
        if not isinstance(parts_val, list):
            return
        for part in parts_val:
            if not isinstance(part, dict):
                continue
            part_dict = as_object_dict(part)
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
                part_dict = as_object_dict(first_part)
                return str(part_dict.get("text", ""))
        return ""


class GeminiParser(NdjsonParserBase):
    __slots__ = ("_dispatcher", "_text_accumulator")
    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset({"done", "stop", "message_end"})

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: TextAccumulator | None = None
        self._dispatcher = _GeminiDispatch(self)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        self._try_register_subagent_pid_from_obj(obj)
        yield from self._dispatcher.dispatch(obj, raw)

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        if event_type in self._STOP_EVENT_TYPES:
            return self._flush_stop_event(obj, event_type)
        return super()._handle_lifecycle_event(obj, event_type)

    def _flush_stop_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self.flush_accumulators()
        yield AgentOutputLine(type="stop", raw=str(event_type), metadata=obj)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        if self._text_accumulator is None:
            return
        acc = self._text_accumulator
        self._text_accumulator = None
        yield from acc.flush(kind="text")


import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


_TEXT_KIND = "text"

_HANDLER_RETURNS_NO_EVENTS = False


def _message_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = as_object_dict(block).get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _tool_call_metadata(tool_call: dict[str, object]) -> tuple[str, dict[str, object], str | None]:
    function = tool_call.get("function")
    name = "unknown"
    if isinstance(function, dict):
        function_dict = as_object_dict(function)
        raw_name = function_dict.get("name")
        if isinstance(raw_name, str) and raw_name:
            name = raw_name
        raw_arguments = function_dict.get("arguments")
        if isinstance(raw_arguments, str) and raw_arguments:
            try:
                decoded: object = json.loads(raw_arguments, strict=False)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                return name, as_object_dict(decoded), _tool_call_id(tool_call)
        if isinstance(raw_arguments, dict):
            return name, as_object_dict(raw_arguments), _tool_call_id(tool_call)
    return name, {}, _tool_call_id(tool_call)


def _tool_call_id(tool_call: dict[str, object]) -> str | None:
    call_id = tool_call.get("id")
    if isinstance(call_id, str) and call_id:
        return call_id
    return None


def _tool_payload_declares_error(obj: dict[str, object]) -> bool:
    for key in ("is_error", "isError"):
        flag = obj.get(key)
        if isinstance(flag, str):
            return flag.casefold() in {"true", "1", "yes"}
        if bool(flag):
            return True
    return False


class KimiParser(NdjsonParserBase):
    __slots__ = ("_text_accumulator",)

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: TextAccumulator = TextAccumulator()

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        role = str(obj.get("role", ""))
        if role == "assistant":
            yield from self._handle_assistant(obj, raw)
            return
        if role == "tool":
            yield from self._handle_tool(obj, raw)
            return
        if role == "meta":
            yield from self._handle_meta(obj, raw)
            return
        if role == "user":
            yield from self._handle_user(obj, raw)
            return
        yield AgentOutputLine(type=role or "unknown", raw=raw, metadata=obj)

    def _handle_assistant(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        text = _message_content_text(obj.get("content"))
        if text:
            yield from self._text_accumulator.accumulate(
                text, raw, kind=_TEXT_KIND, keep_current_when_empty=False
            )
        raw_tool_calls = obj.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            return
        if not any(isinstance(call, dict) for call in raw_tool_calls):
            return
        yield from self.flush_accumulators()
        for call in raw_tool_calls:
            if not isinstance(call, dict):
                continue
            call_dict = as_object_dict(call)
            tool_name, args, call_id = _tool_call_metadata(call_dict)
            metadata: dict[str, object] = {"tool": tool_name, "input": args, **call_dict}
            if call_id is not None:
                metadata["tool_use_id"] = call_id
            summary = self._tool_summary(tool_name, args)
            yield AgentOutputLine(
                type="tool_use",
                content=summary,
                raw=raw,
                metadata=metadata,
            )

    def _tool_summary(self, tool_name: str, args: dict[str, object]) -> str:
        for key in ("file_path", "path", "command", "query", "pattern"):
            value = args.get(key)
            if isinstance(value, str) and value:
                return f"{tool_name} {value}"
        return tool_name

    def _handle_tool(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        if _tool_payload_declares_error(obj):
            error_text = _message_content_text(obj.get("content")) or "tool execution failed"
            yield AgentOutputLine(
                type="error",
                content=error_text,
                raw=raw,
                metadata=obj,
                channel=ContentChannel.TOOL_RESULT,
            )
            return
        yield from self.flush_accumulators()
        content = _message_content_text(obj.get("content"))
        call_id = obj.get("tool_call_id")
        metadata: dict[str, object] = dict(obj)
        if isinstance(call_id, str) and call_id:
            metadata["tool_use_id"] = call_id
            metadata["tool"] = f"tool_call {call_id}"
        else:
            metadata["tool"] = "tool_call"
        yield AgentOutputLine(type="tool_result", content=content, raw=raw, metadata=metadata)

    def _handle_meta(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        meta_type = str(obj.get("type", ""))
        content = _message_content_text(obj.get("content"))
        if meta_type == "system.version":
            version = obj.get("version")
            banner = version if isinstance(version, str) and version else meta_type
            content = content or f"kimi {banner}"
        elif meta_type == "session.resume_hint":
            session_id = obj.get("session_id")
            content = content or (
                f"kimi session {session_id}"
                if isinstance(session_id, str) and session_id
                else "kimi session"
            )
        else:
            content = content or f"kimi meta {meta_type or 'unknown'}"
        yield AgentOutputLine(type="lifecycle", content=content, raw=raw, metadata=obj)

    def _handle_user(
        self,
        _obj: dict[str, object],
        _raw: str,
    ) -> Iterator[AgentOutputLine]:
        if _HANDLER_RETURNS_NO_EVENTS:
            yield from ()
        return

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        yield from self._text_accumulator.flush(kind=_TEXT_KIND)


import contextlib
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


class _OpenCodeDispatch:
    __slots__ = ("_owner",)

    def __init__(self, owner: OpenCodeParser) -> None:
        self._owner = owner

    @staticmethod
    def _canonical_tool_name(raw_tool_name: str) -> str:
        if raw_tool_name.startswith("ralph_"):
            return raw_tool_name[len("ralph_") :]
        return raw_tool_name

    def dispatch(self, obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if event_type == "step_start":
            step_id = str(obj.get("id", ""))
            if step_id:
                self._owner._current_part_id = step_id
            return
        if event_type == "step_finish":
            current = self._owner._current_part_id
            accumulators = self._owner._accumulators
            if current and current in accumulators:
                yield from self._flush_accumulator(current)
            self._owner._current_part_id = None
            return
        if event_type == "done":
            yield from self._owner.flush_accumulators()
            self._owner._current_part_id = None
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

        part_obj = obj.get("part")
        part: dict[str, object] = {}
        if isinstance(part_obj, dict):
            part = as_object_dict(part_obj)

        handler_map = {
            "stream": self._parse_stream,
            "text": self._parse_text,
            "error": self._parse_error,
            "tool_use": self._parse_tool_use,
            "tool_result": self._parse_tool_result,
        }

        handler = handler_map.get(event_type)
        if handler:
            yield from handler(obj, part, stripped)
            return

        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _flush_accumulator(self, key: str) -> Iterator[AgentOutputLine]:
        accumulators = self._owner._accumulators
        if key not in accumulators:
            return
        acc = accumulators.pop(key)
        yield from acc.flush(kind="text")

    def _parse_stream(
        self,
        obj: dict[str, object],
        _part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        content = obj.get("content", "")
        if not isinstance(content, str) or not content:
            return

        part_id = self._owner._current_part_id
        if part_id is None:
            yield AgentOutputLine(type="text", content=content, raw=raw)
            return

        key = part_id
        accumulators = self._owner._accumulators
        if key not in accumulators:
            accumulators[key] = TextAccumulator()
        yield from accumulators[key].accumulate(
            content, raw, kind="text", keep_current_when_empty=True
        )

    def _parse_text(
        self,
        obj: dict[str, object],
        part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        part_text = part.get("text")
        if isinstance(part_text, str) and part_text:
            yield AgentOutputLine(type="text", content=part_text, raw=raw, metadata=obj)
            return

        content = obj.get("content", "")
        if isinstance(content, str) and content:
            yield AgentOutputLine(type="text", content=content, raw=raw, metadata=obj)
            return

        yield AgentOutputLine(type="text", raw=raw, metadata=obj)

    def _parse_error(
        self,
        obj: dict[str, object],
        _part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        error_msg = extract_error_message(obj)
        yield AgentOutputLine(type="error", content=error_msg, raw=raw, metadata=obj)

    def _parse_tool_use(
        self,
        obj: dict[str, object],
        part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        raw_tool_name = str(part.get("tool", obj.get("tool", "unknown")))
        canonical_tool_name = self._canonical_tool_name(raw_tool_name)
        state_obj = part.get("state")
        metadata = self._tool_metadata(obj, part)

        if not isinstance(state_obj, dict):
            yield AgentOutputLine(
                type="tool_use", content=canonical_tool_name, raw=raw, metadata=metadata
            )
            return

        status = str(state_obj.get("status", ""))
        if status == "completed":
            if not self._owner._tool_call_was_dispatched(part):
                yield AgentOutputLine(
                    type="tool_use",
                    content=canonical_tool_name,
                    raw=raw,
                    metadata=metadata,
                )
            self._owner._finish_tool_call(part)
            output = state_obj.get("output", "")
            yield AgentOutputLine(
                type="tool_result",
                content=stringify_text_blocks(output),
                raw=raw,
                metadata=metadata,
            )
            return

        if status == "error":
            if not self._owner._tool_call_was_dispatched(part):
                yield AgentOutputLine(
                    type="tool_use",
                    content=canonical_tool_name,
                    raw=raw,
                    metadata=metadata,
                )
            self._owner._finish_tool_call(part)
            err = str(state_obj.get("error", "tool error"))
            yield AgentOutputLine(
                type="error",
                content=err,
                raw=raw,
                metadata=metadata,
                channel=ContentChannel.TOOL_RESULT,
            )
            return

        self._owner._record_tool_dispatch(part)
        yield AgentOutputLine(
            type="tool_use", content=canonical_tool_name, raw=raw, metadata=metadata
        )

    def _parse_tool_result(
        self,
        obj: dict[str, object],
        part: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        metadata = self._tool_metadata(obj, part)
        if "result" in obj:
            result = stringify_text_blocks(obj.get("result", ""))
        else:
            state_obj = part.get("state")
            result = (
                stringify_text_blocks(state_obj.get("output", ""))
                if isinstance(state_obj, dict)
                else ""
            )
        yield AgentOutputLine(type="tool_result", content=result, raw=raw, metadata=metadata)

    def _tool_metadata(
        self,
        obj: dict[str, object],
        part: dict[str, object],
    ) -> dict[str, object]:
        metadata = dict(obj)
        raw_tool_name = part.get("tool", obj.get("tool"))
        if isinstance(raw_tool_name, str) and raw_tool_name:
            tool_name = self._canonical_tool_name(raw_tool_name)
            metadata["tool"] = tool_name
            metadata["tool_raw"] = raw_tool_name

        call_id = self._owner._tool_call_id(part)
        if call_id is not None:
            metadata["call_id"] = call_id

        input_obj = part.get("input")
        if isinstance(input_obj, dict):
            metadata["input"] = input_obj
            return metadata

        state_obj = part.get("state")
        if isinstance(state_obj, dict):
            nested_input = state_obj.get("input")
            if isinstance(nested_input, dict):
                metadata["input"] = nested_input

        return metadata


class OpenCodeParser(NdjsonParserBase):
    __slots__ = (
        "_current_part_id",
        "_dispatched_tool_call_ids",
        "_dispatcher",
        "_stream_counter",
    )
    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset({"step_start", "step_finish", "done"})

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry = subagent_pid_registry
        self._subagent_source_label = subagent_source_label
        self._accumulators: dict[str, TextAccumulator] = {}
        self._dispatched_tool_call_ids: deque[str] = deque(maxlen=256)
        self._current_part_id: str | None = None
        self._stream_counter = 0
        self._dispatcher = _OpenCodeDispatch(self)

    def _flush_before_error(
        self,
        _obj: dict[str, object],
        _raw: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self.flush_accumulators()
        self._current_part_id = None

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        if event_type == "step_start":
            step_id = obj.get("id")
            if not isinstance(step_id, str):
                part = obj.get("part")
                step_id = part.get("id") if isinstance(part, dict) else None
            if isinstance(step_id, str) and step_id:
                self._current_part_id = step_id
        elif event_type in {"step_finish", "done"}:
            return self._flush_lifecycle(event_type)
        return iter(())

    def _flush_lifecycle(self, event_type: str) -> Iterator[AgentOutputLine]:
        if event_type == "step_finish":
            current = self._current_part_id
            if current and current in self._accumulators:
                yield from self._dispatcher._flush_accumulator(current)
            self._current_part_id = None
            return
        yield from self.flush_accumulators()
        self._current_part_id = None
        yield AgentOutputLine(type="stop", raw=event_type, metadata={"type": event_type})

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        yield from self._dispatcher.dispatch(obj, raw)

    @staticmethod
    def _tool_call_id(part: dict[str, object]) -> str | None:
        for key in ("callID", "callId"):
            value = part.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _record_tool_dispatch(self, part: dict[str, object]) -> None:
        call_id = self._tool_call_id(part)
        if call_id is not None and call_id not in self._dispatched_tool_call_ids:
            self._dispatched_tool_call_ids.append(call_id)

    def _tool_call_was_dispatched(self, part: dict[str, object]) -> bool:
        call_id = self._tool_call_id(part)
        return call_id is not None and call_id in self._dispatched_tool_call_ids

    def _finish_tool_call(self, part: dict[str, object]) -> None:
        call_id = self._tool_call_id(part)
        if call_id is not None:
            with contextlib.suppress(ValueError):
                self._dispatched_tool_call_ids.remove(call_id)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        for key in list(self._accumulators.keys()):
            if key not in self._accumulators:
                continue
            acc = self._accumulators.pop(key)
            yield from acc.flush(kind="text")


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


_TEXT_KIND = "text"
_THINKING_KIND = "thinking"

_TEXT_ACCUMULATOR_PREFIX = "text:"
_THINKING_ACCUMULATOR_PREFIX = "thinking:"

_LEGACY_UNINDEXED_CONTENT_INDEX = -1


def _content_index_of(sub: dict[str, object]) -> int:
    raw = sub.get("contentIndex")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return _LEGACY_UNINDEXED_CONTENT_INDEX


def _accumulator_key(kind: str, content_index: int) -> str:
    return f"{kind}:{content_index}"


def _extract_tool_result_text(result: object) -> str:
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result) if result else ""
    content = result.get("content")
    if not isinstance(content, list):
        return str(result)
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            block_dict = as_object_dict(block)
            if block_dict.get("type") == "text":
                text = block_dict.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


_PI_PASSTHROUGH_TOP_LEVEL_EVENTS: frozenset[str] = frozenset(
    {
        "queue_update",
        "compaction_start",
        "compaction_end",
    }
)


_PI_ERROR_STOP_REASON = "error"


def _message_failure_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    message_dict = as_object_dict(message)
    stop_reason = message_dict.get("stopReason")
    if not isinstance(stop_reason, str):
        return ""
    if stop_reason.casefold() != _PI_ERROR_STOP_REASON:
        return ""
    error_message = message_dict.get("errorMessage")
    if isinstance(error_message, str) and error_message.strip():
        return error_message
    return "model turn failed (stopReason=error)"


_PI_STOP_EVENTS: frozenset[str] = frozenset({"agent_end", "turn_end"})


_PI_SILENT_TOP_LEVEL_EVENTS: frozenset[str] = frozenset(
    {"agent_start", "turn_start", "message_start"}
)


_PI_SILENT_SUB_EVENTS: frozenset[str] = frozenset({"text_start", "thinking_start"})


def _make_passthrough(
    event_type: str,
) -> Callable[[dict[str, object], str], Iterator[AgentOutputLine]]:
    def _handler(obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    return _handler


def _pi_retry_label(attempt: object, max_attempts: object) -> str:
    attempt_int = attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else None
    max_int = (
        max_attempts
        if isinstance(max_attempts, int) and not isinstance(max_attempts, bool)
        else None
    )
    if attempt_int is not None and max_int is not None:
        return f"retry {attempt_int}/{max_int}"
    if attempt_int is not None:
        return f"retry {attempt_int}"
    return "retry"


class _PiDispatch:
    __slots__ = ("_owner", "_sub_event_handlers", "_top_level_handlers")

    def __init__(self, owner: PiParser) -> None:
        self._owner = owner
        self._top_level_handlers: dict[
            str, Callable[[dict[str, object], str], Iterator[AgentOutputLine]]
        ] = {
            "session": self._handle_session,
            "message_end": self._handle_message_end,
            "message_update": self._handle_message_update,
            "tool_execution_start": self._handle_tool_execution_start,
            "tool_execution_update": self._handle_tool_execution_update,
            "tool_execution_end": self._handle_tool_execution_end,
            "extension_error": self._handle_extension_error,
            "auto_retry_start": self._handle_auto_retry_start,
            "auto_retry_end": self._handle_auto_retry_end,
        }
        for _evt in _PI_PASSTHROUGH_TOP_LEVEL_EVENTS:
            self._top_level_handlers[_evt] = _make_passthrough(_evt)
        self._sub_event_handlers: dict[
            str, Callable[[dict[str, object], str], Iterator[AgentOutputLine]]
        ] = {
            "text_delta": self._handle_text_delta,
            "text_end": self._handle_text_end,
            "thinking_delta": self._handle_thinking_delta,
            "thinking_end": self._handle_thinking_end,
            "toolcall_start": self._handle_toolcall_start,
            "toolcall_delta": self._handle_toolcall_delta,
            "toolcall_end": self._handle_toolcall_end,
            "done": self._handle_done,
            "error": self._handle_message_error,
        }

    def dispatch(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if event_type in _PI_SILENT_TOP_LEVEL_EVENTS:
            return

        if event_type in _PI_STOP_EVENTS:
            yield from self._owner.flush_accumulators()
            self._owner.reset_emission_flags()
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

        handler = self._top_level_handlers.get(event_type)
        if handler is not None:
            yield from handler(obj, stripped)
            return

        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _handle_session(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        metadata: dict[str, object] = dict(obj)
        yield AgentOutputLine(type="session", raw=stripped, metadata=metadata)

    def _handle_message_end(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        for key in list(self._owner._accumulators.keys()):
            self._owner._accumulators.pop(key, None)
        yield from self._emit_message_content(obj, stripped)
        failure_text = _message_failure_text(obj.get("message"))
        if failure_text:
            yield AgentOutputLine(
                type="error",
                content=failure_text,
                raw=stripped,
                metadata=obj,
            )
        self._owner.reset_emission_flags()

    def _handle_auto_retry_start(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        attempt = obj.get("attempt")
        max_attempts = obj.get("maxAttempts")
        error_message = obj.get("errorMessage")
        parts: list[str] = [_pi_retry_label(attempt, max_attempts)]
        delay_ms = obj.get("delayMs")
        if isinstance(delay_ms, int) and not isinstance(delay_ms, bool):
            parts.append(f"after {delay_ms}ms")
        if isinstance(error_message, str) and error_message.strip():
            parts.append(f"- {error_message}")
        yield AgentOutputLine(
            type="auto_retry_start",
            content=" ".join(parts),
            raw=stripped,
            metadata=obj,
        )

    def _handle_auto_retry_end(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        success = obj.get("success")
        if success is False:
            final_error = obj.get("finalError")
            content = (
                final_error
                if isinstance(final_error, str) and final_error.strip()
                else "agent retries exhausted"
            )
            yield AgentOutputLine(
                type="error",
                content=content,
                raw=stripped,
                metadata=obj,
            )
            return
        yield AgentOutputLine(type="auto_retry_end", raw=stripped, metadata=obj)

    def _handle_message_update(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        assistant_event = obj.get("assistantMessageEvent")
        if not isinstance(assistant_event, dict):
            return
        sub = as_object_dict(assistant_event)
        sub_type = str(sub.get("type", "unknown"))
        if sub_type in _PI_SILENT_SUB_EVENTS:
            return
        handler = self._sub_event_handlers.get(sub_type)
        if handler is not None:
            yield from handler(sub, stripped)
            return
        yield AgentOutputLine(type=sub_type, raw=stripped, metadata=sub)

    def _handle_tool_execution_start(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        tool_name = str(obj.get("toolName", "unknown"))
        args = obj.get("args", {})
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "args": args, "tool_call_id": obj.get("toolCallId")},
        )

    def _handle_tool_execution_update(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        del obj, stripped
        return iter(())

    def _handle_tool_execution_end(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        is_error = obj.get("isError", False)
        result = obj.get("result", "")
        extracted = _extract_tool_result_text(result)
        if is_error:
            yield AgentOutputLine(
                type="error",
                content=extracted if extracted else "tool execution failed",
                raw=stripped,
                metadata={
                    **obj,
                    "tool": str(obj.get("toolName", "unknown")),
                    "tool_call_id": obj.get("toolCallId"),
                },
                channel=ContentChannel.TOOL_RESULT,
            )
            return
        yield AgentOutputLine(
            type="tool_result",
            content=extracted,
            raw=stripped,
            metadata={
                **obj,
                "tool": str(obj.get("toolName", "unknown")),
                "tool_call_id": obj.get("toolCallId"),
            },
        )

    def _handle_extension_error(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        error_msg = str(obj.get("error", "extension error"))
        yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)

    def _handle_text_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        delta = str(sub.get("delta", ""))
        if not delta:
            return
        content_index = _content_index_of(sub)
        if content_index in self._owner.saw_text_end_by_index:
            return
        acc = self._get_text_accumulator(content_index)
        yield from acc.accumulate(delta, stripped, kind=_TEXT_KIND, keep_current_when_empty=True)

    def _handle_text_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        content = str(sub.get("content", ""))
        content_index = _content_index_of(sub)
        self._owner.saw_text_end_by_index.add(content_index)
        acc_key = _accumulator_key(_TEXT_KIND, content_index)
        if content:
            self._owner._accumulators.pop(acc_key, None)
            yield AgentOutputLine(type=_TEXT_KIND, content=content, raw=stripped, metadata=sub)
            return
        acc = self._owner._accumulators.pop(acc_key, None)
        if acc is not None:
            yield from acc.flush(kind=_TEXT_KIND)

    def _handle_thinking_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        delta = str(sub.get("delta", ""))
        if not delta:
            return
        content_index = _content_index_of(sub)
        if content_index in self._owner.saw_thinking_end_by_index:
            return
        acc = self._get_thinking_accumulator(content_index)
        yield from acc.accumulate(
            delta,
            stripped,
            kind=_THINKING_KIND,
            keep_current_when_empty=True,
        )

    def _handle_thinking_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        content = str(sub.get("content", ""))
        content_index = _content_index_of(sub)
        self._owner.saw_thinking_end_by_index.add(content_index)
        acc_key = _accumulator_key(_THINKING_KIND, content_index)
        if content.strip():
            self._owner._accumulators.pop(acc_key, None)
            yield AgentOutputLine(
                type=_THINKING_KIND,
                content=content,
                raw=stripped,
                metadata=sub,
            )
            return
        acc = self._owner._accumulators.pop(acc_key, None)
        if acc is not None:
            yield from acc.flush(kind=_THINKING_KIND, require_strip=True)

    def _handle_toolcall_start(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        return iter([])

    def _handle_toolcall_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        return iter([])

    def _handle_toolcall_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        tool_call = sub.get("toolCall")
        tool_name = "unknown"
        args: object = {}
        if isinstance(tool_call, dict):
            tool_call_dict = as_object_dict(tool_call)
            tool_name = str(tool_call_dict.get("name", "unknown"))
            args = tool_call_dict.get("arguments", tool_call_dict.get("input", {}))
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "args": args, **sub},
        )

    def _handle_done(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._owner.flush_accumulators()
        self._owner.reset_emission_flags()
        stop_reason = sub.get("stopReason")
        if isinstance(stop_reason, str) and stop_reason.casefold() == "length":
            yield AgentOutputLine(
                type="error",
                content="context length exhausted",
                raw=stripped,
                metadata=sub,
            )
        yield AgentOutputLine(type="stop", raw=stripped, metadata=sub)

    def _handle_message_error(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        reason = str(sub.get("reason", "error"))
        yield AgentOutputLine(type="error", content=reason, raw=stripped, metadata=sub)

    def _emit_message_content(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        message = obj.get("message")
        if not isinstance(message, dict):
            return
        message_dict = as_object_dict(message)
        content = message_dict.get("content")
        if not isinstance(content, list):
            return
        content_list = as_object_list(content)
        for block_index, block in enumerate(content_list):
            if not isinstance(block, dict):
                continue
            block_dict = as_object_dict(block)
            block_type = str(block_dict.get("type", ""))
            if block_type == _TEXT_KIND and block_index not in self._owner.saw_text_end_by_index:
                acc_key = _accumulator_key(_TEXT_KIND, block_index)
                self._owner._accumulators.pop(acc_key, None)
                self._owner.saw_text_end_by_index.add(block_index)
                yield from self._handle_text_block(block_dict, stripped)
            elif (
                block_type == _THINKING_KIND
                and block_index not in self._owner.saw_thinking_end_by_index
            ):
                acc_key = _accumulator_key(_THINKING_KIND, block_index)
                self._owner._accumulators.pop(acc_key, None)
                self._owner.saw_thinking_end_by_index.add(block_index)
                yield from self._handle_thinking_block(block_dict, stripped)
            elif block_type == "toolCall":
                yield from self._handle_toolcall_block(block_dict, stripped)
            elif block_type == "toolResult":
                yield from self._handle_toolresult_block(block_dict, stripped)

    def _handle_text_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        text = str(block_dict.get("text", ""))
        if text:
            yield AgentOutputLine(type="text", content=text, raw=stripped, metadata=block_dict)

    def _handle_thinking_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        thinking = str(block_dict.get("thinking", ""))
        if thinking:
            yield AgentOutputLine(
                type="thinking",
                content=thinking,
                raw=stripped,
                metadata=block_dict,
            )

    def _handle_toolcall_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        tool_name = str(block_dict.get("name", "unknown"))
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata=block_dict,
        )

    def _handle_toolresult_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        is_error = block_dict.get("isError", False)
        result = block_dict.get("result", "")
        extracted = _extract_tool_result_text(result)
        if is_error:
            yield AgentOutputLine(
                type="error",
                content=extracted if extracted else "tool result error",
                raw=stripped,
                metadata=block_dict,
                channel=ContentChannel.TOOL_RESULT,
            )
            return
        yield AgentOutputLine(
            type="tool_result",
            content=extracted,
            raw=stripped,
            metadata=block_dict,
        )

    def _get_text_accumulator(self, content_index: int) -> TextAccumulator:
        accumulators = self._owner._accumulators
        key = _accumulator_key(_TEXT_KIND, content_index)
        if key not in accumulators:
            accumulators[key] = TextAccumulator()
        return accumulators[key]

    def _get_thinking_accumulator(self, content_index: int) -> TextAccumulator:
        accumulators = self._owner._accumulators
        key = _accumulator_key(_THINKING_KIND, content_index)
        if key not in accumulators:
            accumulators[key] = TextAccumulator()
        return accumulators[key]


class PiParser(NdjsonParserBase):
    __slots__ = ("_dispatcher", "saw_text_end_by_index", "saw_thinking_end_by_index")
    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._accumulators: dict[str, TextAccumulator] = {}
        self.saw_text_end_by_index: set[int] = set()
        self.saw_thinking_end_by_index: set[int] = set()
        self._dispatcher = _PiDispatch(self)

    def reset_emission_flags(self) -> None:
        self.saw_text_end_by_index = set()
        self.saw_thinking_end_by_index = set()

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        return None

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        self._try_register_subagent_pid_from_obj(obj)
        yield from self._dispatcher.dispatch(obj, raw)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        keys = list(self._accumulators.keys())
        for key in keys:
            acc = self._accumulators.pop(key, None)
            if acc is None:
                continue
            if key.startswith(_THINKING_ACCUMULATOR_PREFIX):
                yield from acc.flush(kind=_THINKING_KIND, require_strip=True)
            elif key.startswith(_TEXT_ACCUMULATOR_PREFIX):
                yield from acc.flush(kind=_TEXT_KIND)


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


_SHORT_CONTENT_THRESHOLD = 200

_PLAIN_TOOL_PREFIX = "[plain] tool:"


def _classify_plaintext_tool_line(stripped: str) -> tuple[str, str] | None:
    if stripped.startswith(_PLAIN_TOOL_PREFIX):
        tool_name = stripped[len(_PLAIN_TOOL_PREFIX) :].strip()
        return ("tool_use", tool_name)
    return None


class GenericParser(NdjsonParserBase):
    __slots__ = ("_text_accumulator",)
    _STOP_TYPES: frozenset[str] = frozenset({"stop", "done", "complete", "finish", "end"})

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: TextAccumulator | None = None

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        normalized = normalize_vt_text(stripped).strip()
        yield from self._flush_accumulator()
        classification = _classify_plaintext_tool_line(normalized)
        if classification is not None:
            line_type, content = classification
            yield AgentOutputLine(type=line_type, content=content, raw=stripped)
            return
        yield AgentOutputLine(type="raw", content=normalized, raw=stripped)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        self._try_register_subagent_pid_from_obj(obj)
        yield from self._classify_parsed_json(obj, raw)

    def _classify_parsed_json(
        self, obj: dict[str, object], stripped: str
    ) -> Iterator[AgentOutputLine]:
        if self._is_stop(obj):
            yield from self._flush_accumulator()
            yield AgentOutputLine(type="stop", raw=stripped)
            return

        if self._is_error(obj):
            yield from self._flush_accumulator()
            error_msg = extract_error_message(obj)
            yield AgentOutputLine(
                type="error",
                content=error_msg,
                raw=stripped,
                metadata=obj,
            )
            return

        content = self._extract_content(obj)
        if content:
            yield from self._process_content(content, stripped)
            return

        thinking = self._extract_thinking_content(obj)
        if thinking:
            yield from self._flush_accumulator()
            yield AgentOutputLine(type="thinking", content=thinking, raw=stripped, metadata=obj)
            return

        yield from self._flush_accumulator()
        yield AgentOutputLine(type="unknown", raw=stripped, metadata=obj)

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        if event_type in self._STOP_TYPES:
            return self._flush_stop_event(obj, event_type)
        return super()._handle_lifecycle_event(obj, event_type)

    def _flush_stop_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._flush_accumulator()
        yield AgentOutputLine(type="stop", raw=event_type, metadata=obj)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        yield from self._flush_accumulator()

    def _is_short_content(self, content: str) -> bool:
        if len(content) >= _SHORT_CONTENT_THRESHOLD:
            return False
        return not content.endswith("\n\n")

    def _process_content(self, content: str, raw: str) -> Iterator[AgentOutputLine]:
        if not content:
            return

        if content.endswith("\n\n"):
            yield from self._flush_accumulator()
            emit_content = content[:-2]
            if emit_content:
                yield AgentOutputLine(type="text", content=emit_content, raw=raw)
            return

        if self._is_short_content(content):
            if self._text_accumulator is None:
                self._text_accumulator = TextAccumulator()
            yield from self._text_accumulator.accumulate(
                content, raw, kind="text", keep_current_when_empty=False
            )
            return

        yield from self._flush_accumulator()
        yield AgentOutputLine(type="text", content=content, raw=raw)

    def _flush_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._text_accumulator is None:
            return
        acc = self._text_accumulator
        self._text_accumulator = None
        yield from acc.flush(kind="text")

    def _extract_content(self, obj: dict[str, object]) -> str:
        for field_name in ("content", "text", "message", "output", "response", "result"):
            value = obj.get(field_name)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                nested = value.get("text") or value.get("content")
                if isinstance(nested, str) and nested:
                    return nested
        return ""

    def _extract_thinking_content(self, obj: dict[str, object]) -> str:
        for field_name in ("thought", "reasoning"):
            value = obj.get(field_name)
            if isinstance(value, str) and value:
                return value
        return ""

    def _is_error(self, obj: dict[str, object]) -> bool:
        type_val = str(obj.get("type", "")).lower()
        return "error" in type_val or bool(obj.get("error"))

    def _is_stop(self, obj: dict[str, object]) -> bool:
        type_val = str(obj.get("type", "")).lower()
        return type_val in self._STOP_TYPES


import re
from typing import TYPE_CHECKING

from ralph.agents._session_markers import TURN_BOUNDARY_MARKER

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry

_MAX_STATUS_EVENTS = 8
_MAX_TEXT_EVENTS = 64
_TURN_BOUNDARY_MARKER = TURN_BOUNDARY_MARKER
_CTX_PERCENT_RE = re.compile(r"\bctx:\s*\d+%")
_WHITESPACE_RE = re.compile(r"\s+")
_SPINNER_STATUS_RE = re.compile(r"^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s+")
_MULTIPLICATION_SIGN = chr(215)
_EXECUTED_TOOL_RE = re.compile(
    rf"^⚒\s+Executed\s+(?P<tool>\S+)(?:\s+{_MULTIPLICATION_SIGN}\s+\d+)?$"
)


def _status_signature(text: str) -> str:
    stable = _CTX_PERCENT_RE.sub("ctx:%", text)
    return _WHITESPACE_RE.sub(" ", stable).strip()


class NanocoderParser(GenericParser):
    __slots__ = ("_status_signatures", "_text_signatures")

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__(
            subagent_pid_registry=subagent_pid_registry,
            subagent_source_label=subagent_source_label,
        )
        self._status_signatures: tuple[str, ...] = ()
        self._text_signatures: tuple[str, ...] = ()

    def _text_seen(self, signature: str) -> bool:
        if signature in self._text_signatures:
            return True
        if len(self._text_signatures) >= _MAX_TEXT_EVENTS:
            self._text_signatures = (*self._text_signatures[1:], signature)
        else:
            self._text_signatures = (*self._text_signatures, signature)
        return False

    def _is_status_line(self, normalized: str) -> bool:
        if _SPINNER_STATUS_RE.search(normalized):
            return True
        lowered = normalized.lower()
        return (
            "waiting for mcp servers" in lowered
            or "waiting for chat to complete" in lowered
            or " · " in normalized
            or normalized.startswith("~/Library/Preferences/nanocoder/")
        )

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        normalized = normalize_vt_text(stripped).strip()
        yield from self._flush_accumulator()
        if not normalized or normalized == _TURN_BOUNDARY_MARKER:
            return
        if normalized.startswith("[plain] tool:"):
            yield from super()._classify_non_json_line(stripped)
            return

        signature = _status_signature(normalized)
        tool_match = _EXECUTED_TOOL_RE.search(normalized)
        is_status = self._is_status_line(normalized)

        if tool_match is not None:
            yield AgentOutputLine(
                type="tool_use",
                content=tool_match.group("tool"),
                raw=stripped,
                metadata={"event": "nanocoder_tool_execution"},
            )
        elif not is_status and not self._text_seen(signature):
            yield AgentOutputLine(
                type="text",
                content=normalized,
                raw=stripped,
                metadata={"event": "nanocoder_text"},
            )
        elif (
            is_status
            and signature not in self._status_signatures
            and len(self._status_signatures) < _MAX_STATUS_EVENTS
        ):
            self._status_signatures = (*self._status_signatures, signature)
            yield AgentOutputLine(
                type="status",
                content=normalized,
                raw=stripped,
                metadata={"event": "interactive_tui"},
            )


__all__ = [
    "AgyParser",
    "ClaudeParser",
    "CodexParser",
    "CursorParser",
    "GeminiParser",
    "GenericParser",
    "KimiParser",
    "NanocoderParser",
    "NdjsonParserBase",
    "OpenCodeParser",
    "ParserTemplateBase",
    "PiParser",
]
