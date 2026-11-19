from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph._typed_attrs import read_attr

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

type _Frame = dict[str, object]
type _Path = tuple[str, ...]


@dataclass(frozen=True)
class TransportFailureFrameShape:
    cli: str
    matches: Callable[[_Frame], bool]
    message_paths: tuple[_Path, ...]
    status_paths: tuple[_Path, ...] = ()


def _type_is(*names: str) -> Callable[[_Frame], bool]:
    return lambda frame: frame.get("type") in names


def _event_is(name: str) -> Callable[[_Frame], bool]:
    return lambda frame: frame.get("event") == name


def _flagged_result(frame: _Frame) -> bool:
    return frame.get("type") == "result" and frame.get("is_error") is True


def _claude_api_retry(frame: _Frame) -> bool:
    return frame.get("type") == "system" and frame.get("subtype") == "api_retry"


def _pi_failed_message(frame: _Frame) -> bool:
    message = frame.get("message")
    return (
        isinstance(message, dict)
        and isinstance(message.get("stopReason"), str)
        and str(message.get("stopReason")).casefold() == "error"
    )


def _agy_failed_result(frame: _Frame) -> bool:
    result = frame.get("result")
    return (
        frame.get("event") == "result"
        and isinstance(result, dict)
        and result.get("status") not in (None, "SUCCESS")
    )


TRANSPORT_FAILURE_FRAME_SHAPES: tuple[TransportFailureFrameShape, ...] = (
    TransportFailureFrameShape(
        "codex",
        _type_is("error", "stream_error"),
        (("message",),),
    ),
    TransportFailureFrameShape(
        "codex",
        _type_is("turn.failed"),
        (("error", "message"), ("error",)),
    ),
    TransportFailureFrameShape(
        "opencode",
        _type_is("error"),
        (("error", "data", "message"), ("error", "message"), ("error", "name")),
        (("error", "data", "statusCode"),),
    ),
    TransportFailureFrameShape(
        "claude/cursor",
        _flagged_result,
        (("result",), ("error",)),
    ),
    TransportFailureFrameShape(
        "claude",
        _claude_api_retry,
        (("error",),),
        (("error_status",),),
    ),
    TransportFailureFrameShape(
        "pi",
        _pi_failed_message,
        (("message", "errorMessage"),),
    ),
    TransportFailureFrameShape(
        "agy",
        _event_is("error"),
        (("message",), ("error",), ("text",)),
    ),
    TransportFailureFrameShape(
        "agy",
        _agy_failed_result,
        (("result", "error"), ("result", "status")),
    ),
)


def _value_at(frame: _Frame, path: _Path) -> object:
    current: object = frame
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _frame_texts(frame: _Frame) -> list[str]:
    texts: list[str] = []
    for shape in TRANSPORT_FAILURE_FRAME_SHAPES:
        if not shape.matches(frame):
            continue
        texts.extend(
            value
            for path in shape.message_paths
            if isinstance(value := _value_at(frame, path), str) and value
        )
        texts.extend(
            f"status {value}"
            for path in shape.status_paths
            if isinstance(value := _value_at(frame, path), int) and not isinstance(value, bool)
        )
    return texts


def _frames(lines: Iterable[object]) -> list[_Frame]:
    frames: list[_Frame] = []
    for line in lines:
        text = str(line).strip()
        if not text.startswith("{"):
            continue
        try:
            frame: object = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(frame, dict):
            frames.append(frame)
    return frames


def transport_failure_messages(exc: BaseException | None) -> tuple[str, ...]:
    if exc is None:
        return ()
    lines = read_attr(exc, "parsed_output", None)
    if not isinstance(lines, list):
        return ()
    return tuple(text for frame in _frames(lines) for text in _frame_texts(frame))


def terminal_transport_failure_messages(lines: Iterable[object]) -> tuple[str, ...]:
    frames = _frames(lines)
    return tuple(_frame_texts(frames[-1])) if frames else ()


__all__ = [
    "TRANSPORT_FAILURE_FRAME_SHAPES",
    "TransportFailureFrameShape",
    "terminal_transport_failure_messages",
    "transport_failure_messages",
]
