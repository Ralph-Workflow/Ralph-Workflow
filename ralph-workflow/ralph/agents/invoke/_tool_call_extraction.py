from __future__ import annotations

import json
from typing import cast

_PLAIN_TEXT_TOOL_PREFIXES = ("claude tool:", "[plain] tool:")
_CURSOR_TOOL_CALL_METADATA_KEYS = frozenset(
    {
        "toolCallId",
        "startedAtMs",
        "completedAtMs",
        "hookAdditionalContexts",
    }
)


def extract_tool_call_from_activity_signal(
    raw: str,
) -> tuple[str, dict[str, object]] | None:
    plain = _extract_plain_text_tool_call(raw)
    if plain is not None:
        return plain
    top_level = _parse_json_object(raw)
    if top_level is None:
        return None
    nested = _extract_claude_message_tool_use(top_level)
    if nested is not None:
        return _extract_tool_call_from_dict(nested)
    obj = _unwrap_tool_use_envelope(top_level)
    if _is_streaming_tool_placeholder(obj):
        return None
    return _extract_tool_call_from_dict(obj)


def _parse_json_object(raw: str) -> dict[str, object] | None:
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return cast("dict[str, object]", parsed)


def _extract_claude_message_tool_use(obj: dict[str, object]) -> dict[str, object] | None:
    if obj.get("type") not in {"assistant", "user"}:
        return None
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    content = cast("dict[str, object]", message).get("content")
    if not isinstance(content, list):
        return None
    blocks = [
        cast("dict[str, object]", block)
        for block in cast("list[object]", content)
        if isinstance(block, dict) and cast("dict[str, object]", block).get("type") == "tool_use"
    ]
    if len(blocks) != 1:
        return None
    return blocks[0]


def _is_streaming_tool_placeholder(obj: dict[str, object]) -> bool:
    if obj.get("type") != "tool_use":
        return False
    tool_input = obj.get("input")
    return isinstance(tool_input, dict) and not tool_input and "id" in obj


def _extract_plain_text_tool_call(raw: str) -> tuple[str, dict[str, object]] | None:
    stripped = raw.strip()
    if not stripped:
        return None
    lower = stripped.lower()
    for prefix in _PLAIN_TEXT_TOOL_PREFIXES:
        if lower.startswith(prefix):
            tool_name = stripped[len(prefix) :].strip()
            return (tool_name or "unknown"), {}
    return None


def _unwrap_tool_use_envelope(obj: dict[str, object]) -> dict[str, object]:
    if obj.get("type") == "stream_event" or "event" in obj:
        event = obj.get("event")
        if isinstance(event, dict):
            inner_obj = cast("dict[str, object]", event)
            content_block = inner_obj.get("content_block")
            if isinstance(content_block, dict):
                inner_obj = cast("dict[str, object]", content_block)
            return inner_obj
    assistant_event = obj.get("assistantMessageEvent")
    if isinstance(assistant_event, dict):
        return cast("dict[str, object]", assistant_event)
    return obj


def _extract_tool_call_from_dict(
    obj: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    if not _is_tool_use_dict(obj):
        return None
    resolved = _resolve_tool_name_and_args(obj)
    if resolved is None:
        return None
    tool_name, tool_args = resolved
    if tool_name == "unknown" and not tool_args:
        return None
    return resolved


def _is_tool_use_dict(obj: dict[str, object]) -> bool:
    type_field = obj.get("type")
    event_field = obj.get("event")
    return (
        type_field
        in {
            "tool_use",
            "assistant_tool_use",
            "mcp_tool_call",
            "tool_execution_start",
            "toolcall_end",
        }
        or event_field in {"tool_use", "mcp_tool_call"}
        or "tool_name" in obj
        or "toolName" in obj
        or "toolCall" in obj
        or (type_field == "tool_call" and "tool_call" in obj)
    )


def _resolve_tool_name_and_args(
    obj: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    opencode_part = obj.get("part")
    if isinstance(opencode_part, dict):
        return _resolve_opencode_part_tool_call(cast("dict[str, object]", opencode_part))

    cursor_tool_call = obj.get("tool_call")
    if isinstance(cursor_tool_call, dict):
        return _resolve_cursor_tool_call(cursor_tool_call)

    tool_call = obj.get("toolCall")
    if isinstance(tool_call, dict):
        return _resolve_flat_tool_call(cast("dict[str, object]", tool_call))

    return _resolve_flat_tool_call(obj)


def _resolve_flat_tool_call(
    obj: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    tool_name_raw = (
        obj.get("name") or obj.get("tool_name") or obj.get("tool") or obj.get("toolName")
    )
    if tool_name_raw is None:
        tool_name = "unknown"
    elif not isinstance(tool_name_raw, str):
        return None
    else:
        tool_name = tool_name_raw.strip() or "unknown"

    args_field = obj.get("input")
    if not isinstance(args_field, dict):
        args_field = obj.get("arguments")
    if not isinstance(args_field, dict):
        args_field = obj.get("args")
    if not isinstance(args_field, dict):
        args_field = {}
    return tool_name, cast("dict[str, object]", args_field)


def _resolve_opencode_part_tool_call(
    part: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    part_type = part.get("type")
    if part_type is not None and part_type != "tool":
        return None
    tool_name_raw = part.get("tool")
    if not isinstance(tool_name_raw, str):
        return None
    tool_name = tool_name_raw.strip()
    if not tool_name:
        return None

    args_field = part.get("input")
    if not isinstance(args_field, dict):
        state = part.get("state")
        if isinstance(state, dict):
            nested = cast("dict[str, object]", state).get("input")
            if isinstance(nested, dict):
                args_field = nested
    if not isinstance(args_field, dict):
        args_field = {}
    return tool_name, cast("dict[str, object]", args_field)


def _resolve_cursor_tool_call(
    cursor_tool_call: object,
) -> tuple[str, dict[str, object]] | None:
    if not isinstance(cursor_tool_call, dict):
        return None
    cursor_tool_call_dict = cast("dict[str, object]", cursor_tool_call)
    for key, value in cursor_tool_call_dict.items():
        if key in _CURSOR_TOOL_CALL_METADATA_KEYS:
            continue
        if not isinstance(value, dict):
            continue
        payload = cast("dict[str, object]", value)
        args_field = payload.get("args")
        if not isinstance(args_field, dict):
            args_field = payload.get("input")
        if not isinstance(args_field, dict):
            args_field = {}
        return key, _strip_per_call_keys(cast("dict[str, object]", args_field))
    return None


_PER_CALL_ARG_KEYS = frozenset(
    {
        "toolCallId",
        "tool_call_id",
        "callID",
        "call_id",
        "conversationId",
        "conversation_id",
        "startedAtMs",
        "completedAtMs",
    }
)


def _strip_per_call_keys(args: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in args.items() if key not in _PER_CALL_ARG_KEYS}
