from __future__ import annotations

import json
from typing import Final, cast

from ralph.checked_accessors import as_mapping

_SESSION_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"session", "session_ready", "session_start", "session_resume", "thread.started"}
)


def _has_session_identifier(parsed: dict[str, object]) -> bool:
    event_type = parsed.get("type")
    session_event = isinstance(event_type, str) and event_type in _SESSION_EVENT_TYPES
    session_event = session_event or parsed.get("event") == "init"
    session_event = session_event or (
        parsed.get("type") == "system" and parsed.get("subtype") == "init"
    )
    session_event = session_event or (
        parsed.get("role") == "meta" and parsed.get("type") == "session.resume_hint"
    )
    if session_event:
        for key in ("session_id", "sessionId", "conversation_id", "thread_id", "id"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                return True
    opencode_session_id = parsed.get("sessionID")
    if isinstance(opencode_session_id, str) and opencode_session_id:
        return True
    meta = parsed.get("meta")
    if not isinstance(meta, dict):
        return False
    return any(
        isinstance(meta.get(key), str) and bool(meta.get(key))
        for key in ("session_id", "sessionId")
    )


LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "message_start",
        "message_stop",
        "content_block_start",
        "content_block_stop",
        "message_delta",
        "thread.started",
        "turn.started",
        "message_started",
        "heartbeat",
        "ping",
        "ready",
        "start",
        "begin",
        "user",
        "assistant",
        "thinking",
        "step_start",
        "step_finish",
        "done",
    }
)


def is_lifecycle_event(event_type: str) -> bool:
    return event_type in LIFECYCLE_EVENT_TYPES


LIFECYCLE_KINDS: Final[frozenset[str]] = frozenset({"lifecycle"})


def is_lifecycle_kind(kind: str) -> bool:
    return kind in LIFECYCLE_KINDS


def is_session_metadata_event(parsed_json: object) -> bool:
    if not isinstance(parsed_json, dict):
        return False
    try:
        serialized = json.dumps(parsed_json)
    except (TypeError, ValueError):
        return False
    reparsed = cast("object", json.loads(serialized))
    return isinstance(reparsed, dict) and _has_session_identifier(reparsed)


def _event_role(event: object) -> str | None:
    try:
        mapping = as_mapping(event)
        direct_role = mapping.get("role")
        if isinstance(direct_role, str):
            return direct_role
        message = as_mapping(mapping.get("message"))
        nested_role = message.get("role")
        return nested_role if isinstance(nested_role, str) else None
    except TypeError:
        return None


def is_user_prompt_event_line(line: str) -> bool:
    try:
        raw_event = cast("object", json.loads(line))
        event = as_mapping(raw_event)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    return (
        event.get("type") in {"user", "message_start", "message_end"}
        and _event_role(event) == "user"
    )


__all__ = [
    "LIFECYCLE_EVENT_TYPES",
    "LIFECYCLE_KINDS",
    "is_lifecycle_event",
    "is_lifecycle_kind",
    "is_session_metadata_event",
    "is_user_prompt_event_line",
]
