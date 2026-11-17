from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .content_channel import ContentChannel
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry

__all__ = ["KimiParser"]

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
        text = cast("dict[str, object]", block).get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _tool_call_metadata(tool_call: dict[str, object]) -> tuple[str, dict[str, object], str | None]:
    function = tool_call.get("function")
    name = "unknown"
    if isinstance(function, dict):
        function_dict = cast("dict[str, object]", function)
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
                return name, cast("dict[str, object]", decoded), _tool_call_id(tool_call)
        if isinstance(raw_arguments, dict):
            return name, cast("dict[str, object]", raw_arguments), _tool_call_id(tool_call)
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
            call_dict = cast("dict[str, object]", call)
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
