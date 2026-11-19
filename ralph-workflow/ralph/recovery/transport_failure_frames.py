from __future__ import annotations

import json

from ralph._typed_attrs import read_attr

_FAILURE_FRAME_TYPES: frozenset[str] = frozenset({"error", "turn.failed", "stream_error"})


def _frame_message(frame: dict[str, object]) -> str | None:
    if frame.get("type") not in _FAILURE_FRAME_TYPES:
        return None
    message = frame.get("message")
    if isinstance(message, str) and message:
        return message
    error = frame.get("error")
    if isinstance(error, dict):
        nested = error.get("message")
        return nested if isinstance(nested, str) and nested else None
    return error if isinstance(error, str) and error else None


def transport_failure_messages(exc: BaseException | None) -> tuple[str, ...]:
    if exc is None:
        return ()
    lines = read_attr(exc, "parsed_output", None)
    if not isinstance(lines, list):
        return ()
    messages: list[str] = []
    for line in lines:
        text = str(line).strip()
        if not text.startswith("{"):
            continue
        try:
            frame: object = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(frame, dict):
            message = _frame_message(frame)
            if message is not None:
                messages.append(message)
    return tuple(messages)


__all__ = ["transport_failure_messages"]
