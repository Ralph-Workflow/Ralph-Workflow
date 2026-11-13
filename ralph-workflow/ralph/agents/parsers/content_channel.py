from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from ralph.agents.execution_state import is_user_prompt_event_line
from ralph.recovery.failure_classifier import _SUBSCRIPTION_LIMIT_SUBSTRINGS

_PROVIDER_FAILURE_OUTPUT_MARKERS = (
    "retriableerror:",
    "providererror:",
    '"type":"error"',
    '"type": "error"',
)

_NON_PROVIDER_EVENT_TYPES = frozenset(
    {
        "reasoning",
        "subagent",
        "thinking",
        "tool",
        "tool_result",
        "tool_use",
        "user",
    }
)

_TOOL_CALL_EVENT_TYPES = frozenset({"tool", "tool_use", "mcp_tool_call", "function_call"})
_TOOL_RESULT_EVENT_TYPES = frozenset({"tool_result"})
_REASONING_EVENT_TYPES = frozenset({"reasoning", "thinking"})


class ContentChannel(StrEnum):
    PROVIDER = "provider"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    REASONING = "reasoning"
    SUBAGENT = "subagent"
    USER = "user"


def _parse_json_event(line: str) -> dict[str, object] | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed = cast("object", json.loads(stripped))
    except ValueError:
        return {}
    return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else {}


def _is_provider_error_event(event: dict[str, object]) -> bool:
    event_type = event.get("type")
    kind = event_type.casefold() if isinstance(event_type, str) else ""
    if kind in _NON_PROVIDER_EVENT_TYPES:
        return False
    if kind.startswith("item."):
        item = event.get("item")
        item_type = cast("dict[str, object]", item).get("type") if isinstance(item, dict) else None
        return isinstance(item_type, str) and item_type.casefold() == "error"
    if kind == "error":
        return True
    return event.get("error") not in (None, "", {}, [])


def _is_explicit_provider_failure_output(line: str) -> bool:
    event = _parse_json_event(line)
    if event is not None:
        return _is_provider_error_event(event)
    normalized = line.strip().casefold()
    return normalized.startswith(
        ("api quota", "error:", "retriableerror:", "providererror:", "resource_exhausted")
    ) or any(marker in normalized for marker in _PROVIDER_FAILURE_OUTPUT_MARKERS)


def _is_provider_failure_output(line: str) -> bool:
    normalized = line.strip().casefold()
    bare_provider_failure = any(
        normalized.startswith(marker.casefold()) for marker in _SUBSCRIPTION_LIMIT_SUBSTRINGS
    )
    return _is_explicit_provider_failure_output(line) or bare_provider_failure


def _event_kind(event: dict[str, object]) -> str:
    event_type = event.get("type")
    kind = event_type.casefold() if isinstance(event_type, str) else ""
    if kind.startswith("item."):
        item = event.get("item")
        item_type = cast("dict[str, object]", item).get("type") if isinstance(item, dict) else None
        return item_type.casefold() if isinstance(item_type, str) else ""
    return kind


def channel_of(line: str) -> ContentChannel:
    if is_user_prompt_event_line(line):
        return ContentChannel.USER
    event = _parse_json_event(line)
    if event is None:
        return ContentChannel.PROVIDER if _is_provider_failure_output(line) else ContentChannel.USER
    if _is_provider_error_event(event):
        return ContentChannel.PROVIDER
    return _channel_of_known_event(event)


def _channel_of_known_event(event: dict[str, object]) -> ContentChannel:
    kind = _event_kind(event)
    if kind in _TOOL_CALL_EVENT_TYPES:
        return ContentChannel.TOOL_CALL
    if kind in _TOOL_RESULT_EVENT_TYPES:
        return ContentChannel.TOOL_RESULT
    if kind in _REASONING_EVENT_TYPES:
        return ContentChannel.REASONING
    if kind == "subagent":
        return ContentChannel.SUBAGENT
    return ContentChannel.USER


@dataclass(frozen=True, slots=True)
class ProviderText:
    text: str


def provider_text(line: str) -> ProviderText | None:
    if channel_of(line) is ContentChannel.PROVIDER:
        return ProviderText(text=line)
    return None


def provider_evidence_text(detail: str) -> ProviderText | None:
    event = _parse_json_event(detail)
    if event is None or _is_provider_error_event(event):
        return ProviderText(text=detail)
    return None


__all__ = [
    "ContentChannel",
    "ProviderText",
    "channel_of",
    "provider_evidence_text",
    "provider_text",
]
