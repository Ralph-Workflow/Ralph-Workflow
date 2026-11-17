from __future__ import annotations

import contextlib
import json
import re
from typing import cast

from ralph.checked_accessors import as_object_dict
from ralph.display.vt_normalizer import normalize_vt_text


def _as_str_object_dict(value: object) -> dict[str, object] | None:
    try:
        return as_object_dict(value)
    except TypeError:
        return None


def _require_str_object_dict(value: object) -> dict[str, object]:
    mapped = _as_str_object_dict(value)
    if mapped is None:
        msg = "expected a string-keyed mapping"
        raise TypeError(msg)
    return mapped


_PLAIN_TEXT_TOOL_PREFIXES = ("claude tool:", "[plain] tool:")
_MULTIPLICATION_SIGN = chr(215)
_NANOCODER_EXECUTED_TOOL_RE = re.compile(
    rf"^⚒\s+Executed\s+(?P<tool>\S+)(?:\s+{_MULTIPLICATION_SIGN}\s+\d+)?$"
)
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
    if stripped.startswith("data:"):
        stripped = stripped[5:].strip()
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
    blocks: list[dict[str, object]] = []
    for block_obj in content:
        mapped = _as_str_object_dict(block_obj)
        if mapped is not None and mapped.get("type") == "tool_use":
            blocks.append(mapped)
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
    normalized = normalize_vt_text(stripped).strip()
    match = _NANOCODER_EXECUTED_TOOL_RE.match(normalized)
    if match is not None:
        tool_name = match.group("tool")
        return tool_name, {}
    lower = stripped.lower()
    for prefix in _PLAIN_TEXT_TOOL_PREFIXES:
        if lower.startswith(prefix):
            tool_name = stripped[len(prefix) :].strip()
            return (tool_name or "unknown"), {}
    return None


def _unwrap_stream_event_envelope(obj: dict[str, object]) -> dict[str, object]:
    event = obj.get("event")
    if isinstance(event, dict):
        inner_obj = cast("dict[str, object]", event)
        content_block = inner_obj.get("content_block")
        if isinstance(content_block, dict):
            inner_obj = cast("dict[str, object]", content_block)
        return inner_obj
    step_update = obj.get("step_update")
    if isinstance(step_update, dict):
        tool_info = step_update.get("tool_info")
        if isinstance(tool_info, dict):
            merged: dict[str, object] = dict(tool_info)
            merged.setdefault("tool_name", step_update.get("tool_name", merged.get("name")))
            return merged
        return cast("dict[str, object]", step_update)
    return obj


def _unwrap_gemini_function_call(obj: dict[str, object]) -> dict[str, object] | None:
    function_call = obj.get("function_call")
    if isinstance(function_call, dict):
        unwrapped = dict(cast("dict[str, object]", function_call))
        unwrapped.setdefault("type", "function_call")
        return unwrapped
    candidates = obj.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            content = first.get("content")
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, dict) and isinstance(part.get("function_call"), dict):
                            unwrapped = dict(cast("dict[str, object]", part["function_call"]))
                            unwrapped.setdefault("type", "function_call")
                            return unwrapped
    parts = obj.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("function_call"), dict):
                unwrapped = dict(cast("dict[str, object]", part["function_call"]))
                unwrapped.setdefault("type", "function_call")
                return unwrapped
    return None


def _unwrap_tool_calls_list(obj: dict[str, object]) -> dict[str, object] | None:
    tool_calls = obj.get("tool_calls")
    if not (isinstance(tool_calls, list) and tool_calls):
        return None
    first = tool_calls[0]
    if not isinstance(first, dict):
        return None
    func = first.get("function")
    if isinstance(func, dict):
        unwrapped = dict(cast("dict[str, object]", func))
        unwrapped.setdefault("type", "function_call")
        args_str = unwrapped.get("arguments")
        if isinstance(args_str, str):
            with contextlib.suppress(json.JSONDecodeError, ValueError):
                decoded = cast("object", json.loads(args_str))
                if isinstance(decoded, dict):
                    unwrapped["arguments"] = cast("dict[str, object]", decoded)
        return unwrapped
    return cast("dict[str, object]", first)


def _unwrap_tool_use_envelope(obj: dict[str, object]) -> dict[str, object]:
    if obj.get("type") == "stream_event" or "event" in obj:
        return _unwrap_stream_event_envelope(obj)
    content_block = obj.get("content_block")
    if obj.get("type") == "content_block_start" and isinstance(content_block, dict):
        return cast("dict[str, object]", content_block)
    for key in ("assistantMessageEvent", "item"):
        inner = obj.get(key)
        if isinstance(inner, dict):
            return cast("dict[str, object]", inner)
    gemini_func = _unwrap_gemini_function_call(obj)
    if gemini_func is not None:
        return gemini_func
    tool_calls = _unwrap_tool_calls_list(obj)
    if tool_calls is not None:
        return tool_calls
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
            "tool_call",
            "assistant_tool_use",
            "mcp_tool_call",
            "function_call",
            "command_execution",
            "tool_execution_start",
            "toolcall_end",
        }
        or event_field in {"tool_use", "tool_call", "mcp_tool_call", "function_call"}
        or "tool_name" in obj
        or "toolName" in obj
        or "toolCall" in obj
        or "function_call" in obj
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

    function_call = obj.get("function_call")
    if isinstance(function_call, dict):
        return _resolve_flat_tool_call(cast("dict[str, object]", function_call))

    return _resolve_flat_tool_call(obj)


def _resolve_flat_tool_call(
    obj: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    if obj.get("type") == "command_execution":
        command = obj.get("command")
        if isinstance(command, str) and command:
            return "bash", {"command": command}
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
        args_field = obj.get("parameters")
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
