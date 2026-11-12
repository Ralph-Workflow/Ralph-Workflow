from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from pygments.lexers import get_lexer_by_name

from ralph.display.language_inference import lexer_for_path
from ralph.display.vt_normalizer import normalize_vt_text

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


__all__ = ["AgyParser"]

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
        except Exception:
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
    params_dict = cast("dict[str, object]", params)
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
        info = cast("dict[str, object]", raw_info)
        raw_subagents = info.get("subagents")
        if not isinstance(raw_subagents, list):
            return []
        subagents = cast("list[object]", raw_subagents)
        results: list[tuple[str, dict[str, object], str | None, bool]] = []
        for position, sub in enumerate(subagents):
            if not isinstance(sub, dict):
                continue
            details = cast("dict[str, object]", sub)
            cid = f"{step_index}:{position}" if step_index is not None else str(position)
            results.append(("subagent", details, cid, False))
        return results

    raw_info = step.get("tool_info")
    if not isinstance(raw_info, dict):
        return []
    info = cast("dict[str, object]", raw_info)
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
                yield from self._dispatch_step_update(cast("dict[str, object]", update), raw)
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
        res_dict = cast("dict[str, object]", res)
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
            init_dict = cast("dict[str, object]", init_info)
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
            usage_dict = cast("dict[str, object]", usage)
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
            self._pending_text_usage = cast("dict[str, object]", usage)
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

        if step.get("state") == "DONE":
            if call_id is None or call_id not in self._emitted_tool_use_ids:
                if call_id:
                    self._emitted_tool_use_ids.add(call_id)
                yield AgentOutputLine(
                    type="tool_use", content=content_label, raw=raw, metadata=metadata
                )
            output = info.get("output")
            result_content = (
                output
                if isinstance(output, str) and output
                else _completion_summary(content_label, step, info)
            )
            yield AgentOutputLine(
                type="tool_result", content=result_content, raw=raw, metadata=metadata
            )
            return
        if call_id:
            if call_id in self._emitted_tool_use_ids:
                return
            self._emitted_tool_use_ids.add(call_id)
        yield AgentOutputLine(type="tool_use", content=content_label, raw=raw, metadata=metadata)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        yield from self._flush_text()
