from __future__ import annotations

import json
import re
from typing import cast

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.display.vt_normalizer import normalize_vt_text

from ._base import BaseExecutionStrategy
from ._helpers import (
    _error_message_from_error_field,
    _progress_report_signal,
)

_MULTIPLICATION_SIGN = chr(215)
_NANOCODER_EXECUTED_TOOL_RE = re.compile(
    rf"^⚒\s+Executed\s+(?P<tool>\S+)(?:\s+{_MULTIPLICATION_SIGN}\s+\d+)?$"
)
_PLAIN_TOOL_PREFIX = "[plain] tool:"

_RAW_TOOL_USE_TYPES = frozenset(
    {
        "tool_use",
        "tool_call",
        "function_call",
        "mcp_tool_call",
        "tool_execution_start",
    }
)
_RAW_TOOL_RESULT_TYPES = frozenset(
    {
        "tool_result",
        "mcp_tool_result",
        "tool_execution_end",
    }
)
_ITEM_TOOL_TYPES = frozenset(
    {
        "mcp_tool_call",
        "command_execution",
        "tool_use",
        "tool_call",
        "function_call",
    }
)


def _classify_item_event(
    obj: dict[str, object],
    line: str,
) -> AgentActivitySignal | None:
    item = obj.get("item")
    if not isinstance(item, dict):
        return None
    item_dict = cast("dict[str, object]", item)
    item_type = str(item_dict.get("type", ""))
    if item_type not in _ITEM_TOOL_TYPES:
        return None
    error_msg = str(item_dict.get("error")) if item_dict.get("error") else None
    event_type = str(obj.get("type", obj.get("event", "")))
    if event_type == "item.completed":
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line, error_message=error_msg)
    return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line, error_message=error_msg)


def _classify_gemini_event(
    obj: dict[str, object],
    line: str,
) -> AgentActivitySignal | None:
    candidates = obj.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            content = cast("dict[str, object]", first).get("content")
            if isinstance(content, dict):
                cand_parts = cast("dict[str, object]", content).get("parts")
                if isinstance(cand_parts, list) and any(
                    isinstance(p, dict) and "function_call" in p for p in cand_parts
                ):
                    return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

    parts = obj.get("parts")
    if isinstance(parts, list) and any(isinstance(p, dict) and "function_call" in p for p in parts):
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

    if "function_call" in obj:
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

    return None


def _classify_generic_event_type(
    obj: dict[str, object],
    line: str,
) -> AgentActivitySignal | None:
    event_type = str(obj.get("type", obj.get("event", "")))

    if obj.get("subtype") == "completed" and event_type in _RAW_TOOL_USE_TYPES:
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line)

    if event_type in _RAW_TOOL_USE_TYPES:
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

    if event_type in _RAW_TOOL_RESULT_TYPES:
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line)

    if "tool_name" in obj or "toolName" in obj or "toolCall" in obj:
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

    return None


def _classify_generic_tool_frame(
    obj: dict[str, object],
    line: str,
) -> AgentActivitySignal | None:
    return (
        _classify_item_event(obj, line)
        or _classify_gemini_event(obj, line)
        or _classify_generic_event_type(obj, line)
    )


class GenericExecutionStrategy(BaseExecutionStrategy):
    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        stripped = line.strip()
        if not stripped:
            return None

        normalized = normalize_vt_text(stripped).strip()
        if _NANOCODER_EXECUTED_TOOL_RE.match(normalized) or normalized.startswith(
            _PLAIN_TOOL_PREFIX
        ):
            return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

        progress_signal = _progress_report_signal(line)
        if progress_signal is not None:
            return progress_signal

        payload = stripped[5:].strip() if stripped.startswith("data:") else stripped
        if payload.startswith("{"):
            try:
                parsed = cast("object", json.loads(payload, strict=False))
            except (json.JSONDecodeError, ValueError):
                parsed = None

            if isinstance(parsed, dict):
                obj = cast("dict[str, object]", parsed)
                if obj.get("type") == "error":
                    return AgentActivitySignal(
                        AgentActivityKind.ERROR_LINE, raw=_error_message_from_error_field(obj)
                    )
                tool_signal = _classify_generic_tool_frame(obj, line)
                if tool_signal is not None:
                    return tool_signal

        return super().classify_activity_line(line)
