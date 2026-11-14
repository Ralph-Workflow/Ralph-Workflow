from __future__ import annotations

import json
from typing import Final


def _extract_transport_session_id_inline(
    raw_output: list[str] | tuple[str, ...],
) -> str | None:
    from ralph.agents.invoke._session import extract_transport_session_id

    return extract_transport_session_id(raw_output)


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
    return _extract_transport_session_id_inline((serialized,)) is not None


__all__ = [
    "LIFECYCLE_EVENT_TYPES",
    "LIFECYCLE_KINDS",
    "is_lifecycle_event",
    "is_lifecycle_kind",
    "is_session_metadata_event",
]
