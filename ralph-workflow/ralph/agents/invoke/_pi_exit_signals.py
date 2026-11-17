from __future__ import annotations

import json

_PI_CONTEXT_EXHAUSTED_STOP_REASON = "length"
_PI_PROVIDER_FAILURE_STOP_REASON = "error"
_PI_PROVIDER_FAILURE_FALLBACK_REASON = "provider reported an unspecified failure"


def _as_str_object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            result[key] = item
    return result


def _is_pi_agent(agent_name: str) -> bool:
    normalized = agent_name.casefold()
    return normalized == "pi" or normalized.startswith("pi/")


def _message_has_length_stop_reason(message: object) -> bool:
    message_dict = _as_str_object_dict(message)
    if message_dict is None:
        return False
    stop_reason = message_dict.get("stopReason")
    return isinstance(stop_reason, str) and (
        stop_reason.casefold() == _PI_CONTEXT_EXHAUSTED_STOP_REASON
    )


def _line_has_pi_context_exhaustion(line: str) -> bool:
    try:
        parsed: object = json.loads(line)
    except json.JSONDecodeError:
        return False
    obj = _as_str_object_dict(parsed)
    if obj is None:
        return False
    assistant_event = obj.get("assistantMessageEvent")
    event_dict = _as_str_object_dict(assistant_event)
    if event_dict is not None:
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
    message_dict = _as_str_object_dict(message)
    if message_dict is None:
        return None
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
    for message in messages:
        reason = _message_provider_failure_reason(message)
        if reason is not None:
            return reason
    return None


def _line_provider_failure_reason(line: str) -> str | None:
    try:
        parsed: object = json.loads(line)
    except json.JSONDecodeError:
        return None
    obj = _as_str_object_dict(parsed)
    if obj is None:
        return None
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
