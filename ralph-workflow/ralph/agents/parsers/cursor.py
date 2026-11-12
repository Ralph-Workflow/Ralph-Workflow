from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message
from .text_accumulator import TextAccumulator

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
    tool_call_dict = cast("dict[str, object]", tool_call)
    for key, value in tool_call_dict.items():
        if key in _CURSOR_TOOL_CALL_METADATA_KEYS:
            continue
        if not isinstance(value, dict):
            continue
        return key, cast("dict[str, object]", value)
    return None


def _cursor_tool_name_and_args(obj: dict[str, object]) -> tuple[str, dict[str, object]]:
    nested = _cursor_nested_tool_call(obj)
    if nested is not None:
        tool_name, payload = nested
        args = payload.get("args", payload.get("input", {}))
        if not isinstance(args, dict):
            args = {}
        if tool_name == _CURSOR_MCP_WRAPPER_TOOL_NAME:
            return _cursor_mcp_tool_name_and_args(cast("dict[str, object]", args))
        return tool_name, cast("dict[str, object]", args)

    tool_name = str(obj.get("toolName", obj.get("name", "unknown")))
    args = obj.get("args", obj.get("input", {}))
    if not isinstance(args, dict):
        args = {}
    return tool_name, cast("dict[str, object]", args)


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
    return tool_name, cast("dict[str, object]", inner_args)


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
    result_dict = cast("dict[str, object]", result)
    success = result_dict.get("success")
    if isinstance(success, dict):
        success_dict = cast("dict[str, object]", success)
        return (
            _cursor_content_text(success_dict.get("content"))
            or _string_field(success_dict, "text")
            or _string_field(success_dict, "message")
            or _string_field(success_dict, "path")
            or str(result)
        )
    error = result_dict.get("error")
    if isinstance(error, dict):
        error_dict = cast("dict[str, object]", error)
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
    item_dict = cast("dict[str, object]", item)
    text = item_dict.get("text")
    if isinstance(text, str) and text:
        return text
    if isinstance(text, dict):
        nested_text = cast("dict[str, object]", text).get("text")
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
        message_dict = cast("dict[str, object]", message)
        content = message_dict.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            block_dict = cast("dict[str, object]", block)
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
            yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)
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


__all__ = ["CursorParser"]
