from __future__ import annotations

import json
import re
from typing import cast

from ralph.agents._session_markers import TURN_BOUNDARY_MARKER
from ralph.agents.invoke._pty_helpers import _visible_tui_text

_EXPLICIT_COMPLETION_MARKER: str = "Task declared complete:"

_TRANSPORT_SESSION_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^Claude session ready\. Session ID:\s*([A-Za-z0-9._:-]+)$"),
    re.compile(r"^Session ID:\s*([A-Za-z0-9._:-]+)$", re.IGNORECASE),
    re.compile(r"^Resume this session with --resume\s+([A-Za-z0-9._:-]+)$"),
    re.compile(r"^--resume\s+([A-Za-z0-9._:-]+)$"),
    re.compile(r"^--session\s+([A-Za-z0-9._:-]+)$"),
)

_COMPLETION_SESSION_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"session_id\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
    re.compile(r"sessionId\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
)


def extract_transport_text_session_id(stripped: str) -> str | None:
    if _EXPLICIT_COMPLETION_MARKER in stripped:
        for pattern in _COMPLETION_SESSION_ID_PATTERNS:
            match = pattern.search(stripped)
            if match is not None:
                return match.group(1)
    for pattern in _TRANSPORT_SESSION_TEXT_PATTERNS:
        match = pattern.search(stripped)
        if match is not None:
            return match.group(1)
    return None


_COMPLETION_HEAD_PATTERN: re.Pattern[str] = re.compile(
    r"^Task declared complete:\s*session_?id\s*[:=]\s*[A-Za-z0-9._:-]+\s*,\s*summary\s*=",
    re.IGNORECASE,
)

_MAX_TIMESTAMP_DIGITS = 10

_COMPLETION_TAIL_PATTERN: re.Pattern[str] = re.compile(
    rf"'\s*,\s*timestamp\s*[:=]\s*\d{{1,{_MAX_TIMESTAMP_DIGITS}}}$",
    re.IGNORECASE,
)

_SUMMARY_DELIMITER = "'"


_CANONICAL_LINE_OPENINGS: tuple[str, ...] = (
    "claude session ready.",
    "session id:",
    "resume this session with --resume",
    "--resume",
    "--session",
    _EXPLICIT_COMPLETION_MARKER.lower(),
)

_MAX_CANONICAL_OPENING_CHARS = 64


def starts_with_canonical_session_marker(stripped: str) -> bool:
    opening = stripped[:_MAX_CANONICAL_OPENING_CHARS].lower()
    return any(opening.startswith(marker) for marker in _CANONICAL_LINE_OPENINGS)


def is_whole_canonical_session_line(stripped: str) -> bool:
    for pattern in _TRANSPORT_SESSION_TEXT_PATTERNS:
        if pattern.match(stripped) is not None:
            return True
    return _is_whole_completion_line(stripped)


def _is_whole_completion_line(stripped: str) -> bool:
    head = _COMPLETION_HEAD_PATTERN.match(stripped)
    if head is None:
        return False
    tail = _COMPLETION_TAIL_PATTERN.search(stripped)
    if tail is None or tail.start() < head.end():
        return False
    summary = stripped[head.end() : tail.start()]
    return summary.startswith(_SUMMARY_DELIMITER) and _SUMMARY_DELIMITER not in summary[1:]


def is_canonical_session_text_line(stripped: str) -> bool:
    return extract_transport_text_session_id(stripped) is not None


_TRANSPORT_JSON_TYPES = frozenset(
    {
        "session",
        "session_ready",
        "session_start",
        "session_resume",
        "thread.started",
    }
)


def _is_cursor_system_init_event(parsed: dict[str, object]) -> bool:
    return parsed.get("type") == "system" and parsed.get("subtype") == "init"


def _is_kimi_meta_session_frame(parsed: dict[str, object]) -> bool:
    return parsed.get("role") == "meta" and parsed.get("type") == "session.resume_hint"


def _match_transport_json_session_id(parsed: dict[str, object]) -> str | None:
    event_type = parsed.get("type")
    is_agy_init = parsed.get("event") == "init"
    if (
        (isinstance(event_type, str) and event_type in _TRANSPORT_JSON_TYPES)
        or _is_cursor_system_init_event(parsed)
        or is_agy_init
        or _is_kimi_meta_session_frame(parsed)
    ):
        for key in ("session_id", "sessionId", "conversation_id", "thread_id", "id"):
            session_id = parsed.get(key)
            if isinstance(session_id, str) and session_id:
                return session_id
    opencode_session_id = parsed.get("sessionID")
    if isinstance(opencode_session_id, str) and opencode_session_id:
        return opencode_session_id
    return _session_id_from_nested_meta(parsed)


def _session_id_from_nested_meta(parsed: dict[str, object]) -> str | None:
    meta = parsed.get("meta")
    if not isinstance(meta, dict):
        return None
    for key in ("session_id", "sessionId"):
        session_id = meta.get(key)
        if isinstance(session_id, str) and session_id:
            return session_id
    return None


def _extract_transport_session_id_from_line(line: str) -> str | None:
    try:
        parsed = cast("object", json.loads(line))
    except json.JSONDecodeError:
        return extract_transport_text_session_id(line.strip())
    if not isinstance(parsed, dict):
        return None
    return _match_transport_json_session_id(parsed)


def extract_transport_session_id(raw_output: list[str] | tuple[str, ...]) -> str | None:
    for line in raw_output:
        session_id = _extract_transport_session_id_from_line(line)
        if session_id:
            return session_id
    return None


def extract_transport_session_id_from_line(line: str) -> str | None:
    return _extract_transport_session_id_from_line(line)


def extract_transport_session_id_with_visible_tui(line: str) -> str | None:
    primary = extract_transport_session_id_from_line(line)
    if primary:
        return primary
    visible_line = _visible_tui_text(line)
    if visible_line and visible_line != line.strip():
        return extract_transport_text_session_id(visible_line)
    return None


def extract_visible_tui_transport_session_id(text: str) -> str | None:
    return extract_transport_text_session_id(text.strip())


def _bounded_output_lines(
    raw_output: list[str] | tuple[str, ...],
    *,
    explicit_completion_seen: bool = False,
) -> list[str]:
    lines = list(raw_output)
    if explicit_completion_seen and not any(_EXPLICIT_COMPLETION_MARKER in line for line in lines):
        lines.append(_EXPLICIT_COMPLETION_MARKER)
    return lines


__all__ = [
    "TURN_BOUNDARY_MARKER",
    "_EXPLICIT_COMPLETION_MARKER",
    "_bounded_output_lines",
    "extract_transport_session_id",
    "extract_transport_session_id_from_line",
    "extract_transport_session_id_with_visible_tui",
    "extract_visible_tui_transport_session_id",
]
