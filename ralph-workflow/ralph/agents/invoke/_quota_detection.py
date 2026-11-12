from __future__ import annotations

import json
from typing import cast

from ralph.agents.execution_state import is_user_prompt_event_line
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.recovery.failure_classifier import (
    _SUBSCRIPTION_LIMIT_SUBSTRINGS,
    _is_subscription_limit_message,
)

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


def _is_provider_failure_output(line: str) -> bool:
    normalized = line.strip().casefold()
    bare_provider_failure = any(
        normalized.startswith(marker.casefold()) for marker in _SUBSCRIPTION_LIMIT_SUBSTRINGS
    )
    return _is_explicit_provider_failure_output(line) or bare_provider_failure


def _is_explicit_provider_failure_output(line: str) -> bool:
    event = _parse_json_event(line)
    if event is not None:
        return _is_provider_error_event(event)
    normalized = line.strip().casefold()
    return normalized.startswith(
        ("api quota", "error:", "retriableerror:", "providererror:", "resource_exhausted")
    ) or any(marker in normalized for marker in _PROVIDER_FAILURE_OUTPUT_MARKERS)


def is_quota_kill_evidence(detail: str) -> bool:
    event = _parse_json_event(detail)
    return event is None or _is_provider_error_event(event)


def is_provider_quota_output_line(line: str) -> bool:
    normalized = line.strip().casefold()
    return (
        not is_user_prompt_event_line(line)
        and (
            _is_explicit_provider_failure_output(line)
            or any(normalized == marker.casefold() for marker in _SUBSCRIPTION_LIMIT_SUBSTRINGS)
        )
        and _is_subscription_limit_message([line])
    )


def raise_if_quota_exhausted(
    agent_name: str,
    stderr_text: str,
    parsed_output: list[str] | None,
    *,
    include_output: bool,
) -> None:
    output = parsed_output or []
    for line in stderr_text.splitlines() or [stderr_text]:
        if _is_subscription_limit_message([line]):
            raise QuotaExhaustedError(agent_name, line)
    for source in output:
        for line in source.splitlines() or [source]:
            if (
                not is_user_prompt_event_line(line)
                and _is_provider_failure_output(line)
                and (include_output or _is_explicit_provider_failure_output(line))
                and _is_subscription_limit_message([line])
            ):
                raise QuotaExhaustedError(agent_name, line)


__all__ = ["is_provider_quota_output_line", "is_quota_kill_evidence", "raise_if_quota_exhausted"]
