from __future__ import annotations

import json
from typing import cast

_PI_CONTEXT_EXHAUSTED_STOP_REASON = "length"
_PI_PROVIDER_FAILURE_STOP_REASON = "error"
_PI_PROVIDER_FAILURE_FALLBACK_REASON = "provider reported an unspecified failure"


def _is_pi_agent(agent_name: str) -> bool:
    normalized = agent_name.casefold()
    return normalized == "pi" or normalized.startswith("pi/")


def _message_has_length_stop_reason(message: object) -> bool:
    if not isinstance(message, dict):
        return False
    stop_reason = message.get("stopReason")
    return isinstance(stop_reason, str) and (
        stop_reason.casefold() == _PI_CONTEXT_EXHAUSTED_STOP_REASON
    )


def _line_has_pi_context_exhaustion(line: str) -> bool:
    try:
        parsed = cast("object", json.loads(line))
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    obj = cast("dict[str, object]", parsed)
    assistant_event = obj.get("assistantMessageEvent")
    if isinstance(assistant_event, dict):
        event_dict = cast("dict[str, object]", assistant_event)
        event_type = event_dict.get("type")
        stop_reason = event_dict.get("stopReason")
        return (
            event_type == "done"
            and isinstance(stop_reason, str)
            and stop_reason.casefold() == _PI_CONTEXT_EXHAUSTED_STOP_REASON
        )
    if _message_has_length_stop_reason(obj.get("message")):
        return True
    messages = obj.get("messages")
    if isinstance(messages, list):
        return any(_message_has_length_stop_reason(message) for message in messages)
    return False


def has_pi_context_exhaustion_signal(agent_name: str, output: list[str]) -> bool:
    if not _is_pi_agent(agent_name):
        return False
    return any(_line_has_pi_context_exhaustion(line) for line in output)


def _message_provider_failure_reason(message: object) -> str | None:
    if not isinstance(message, dict):
        return None
    message_dict = cast("dict[str, object]", message)
    stop_reason = message_dict.get("stopReason")
    if not isinstance(stop_reason, str):
        return None
    if stop_reason.casefold() != _PI_PROVIDER_FAILURE_STOP_REASON:
        return None
    error_message = message_dict.get("errorMessage")
    if isinstance(error_message, str) and error_message.strip():
        return error_message
    return _PI_PROVIDER_FAILURE_FALLBACK_REASON


def _exhausted_retry_ladder_reason(obj: dict[str, object]) -> str | None:
    if obj.get("type") != "auto_retry_end" or obj.get("success") is not False:
        return None
    final_error = obj.get("finalError")
    if isinstance(final_error, str) and final_error.strip():
        return final_error
    return _PI_PROVIDER_FAILURE_FALLBACK_REASON


def _messages_provider_failure_reason(obj: dict[str, object]) -> str | None:
    reason = _message_provider_failure_reason(obj.get("message"))
    if reason is not None:
        return reason
    messages = obj.get("messages")
    if not isinstance(messages, list):
        return None
    for message in cast("list[object]", messages):
        reason = _message_provider_failure_reason(message)
        if reason is not None:
            return reason
    return None


def _line_provider_failure_reason(line: str) -> str | None:
    try:
        parsed = cast("object", json.loads(line))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    return _exhausted_retry_ladder_reason(obj) or _messages_provider_failure_reason(obj)


def pi_provider_failure_reason(agent_name: str, output: list[str]) -> str | None:
    if not _is_pi_agent(agent_name):
        return None
    for line in output:
        reason = _line_provider_failure_reason(line)
        if reason is not None:
            return reason
    return None


__all__ = ["has_pi_context_exhaustion_signal", "pi_provider_failure_reason"]
