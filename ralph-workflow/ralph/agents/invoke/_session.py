"""Session ID extraction and bounded output utilities."""

from __future__ import annotations

import json
import re
from typing import cast

_EXPLICIT_COMPLETION_MARKER = "Task declared complete:"
_TURN_BOUNDARY_MARKER = "[claude turn boundary]"
_LEGACY_SESSION_ID_PATTERNS = (
    re.compile(r"session\s+id\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
    re.compile(r"session_id\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
    re.compile(r"sessionId\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
    re.compile(r"--resume\s+([A-Za-z0-9._:-]+)"),
    re.compile(r"--session\s+([A-Za-z0-9._:-]+)"),
)

_COMPLETION_SESSION_ID_PATTERNS = (
    re.compile(r"session_id\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
    re.compile(r"sessionId\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
)

_TRANSPORT_TEXT_SESSION_PATTERNS = (
    re.compile(r"^Claude session ready\. Session ID:\s*([A-Za-z0-9._:-]+)$"),
    re.compile(r"^Session ID:\s*([A-Za-z0-9._:-]+)$", re.IGNORECASE),
    re.compile(r"^Resume this session with --resume\s+([A-Za-z0-9._:-]+)$"),
    re.compile(r"^--resume\s+([A-Za-z0-9._:-]+)$"),
    re.compile(r"^--session\s+([A-Za-z0-9._:-]+)$"),
)

_TRANSPORT_JSON_TYPES = frozenset(
    {
        "session",
        "session_ready",
        "session_start",
        "session_resume",
    }
)


def _match_transport_text_session_id(stripped: str) -> str | None:
    if _EXPLICIT_COMPLETION_MARKER in stripped:
        for pattern in _COMPLETION_SESSION_ID_PATTERNS:
            match = pattern.search(stripped)
            if match is not None:
                return match.group(1)
    for pattern in _TRANSPORT_TEXT_SESSION_PATTERNS:
        match = pattern.search(stripped)
        if match is not None:
            return match.group(1)
    return None


def _match_transport_json_session_id(parsed: dict[str, object]) -> str | None:
    event_type = parsed.get("type")
    if isinstance(event_type, str) and event_type in _TRANSPORT_JSON_TYPES:
        for key in ("session_id", "sessionId"):
            session_id = parsed.get(key)
            if isinstance(session_id, str) and session_id:
                return session_id
    meta = parsed.get("meta")
    if not isinstance(meta, dict):
        return None
    for key in ("session_id", "sessionId"):
        session_id = meta.get(key)
        if isinstance(session_id, str) and session_id:
            return session_id
    return None


def _find_session_id(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("session_id", "sessionId"):
            session_id = value.get(key)
            if isinstance(session_id, str) and session_id:
                return session_id
        for nested in value.values():
            session_id = _find_session_id(nested)
            if session_id:
                return session_id
    if isinstance(value, list):
        for item in value:
            session_id = _find_session_id(item)
            if session_id:
                return session_id
    return None


def _extract_session_id_from_line(line: str) -> str | None:
    try:
        parsed = cast("object", json.loads(line))
    except json.JSONDecodeError:
        stripped = line.strip()
        if _EXPLICIT_COMPLETION_MARKER in stripped:
            for pattern in _COMPLETION_SESSION_ID_PATTERNS:
                match = pattern.search(stripped)
                if match is not None:
                    return match.group(1)
        for pattern in _LEGACY_SESSION_ID_PATTERNS:
            match = pattern.search(stripped)
            if match is not None:
                return match.group(1)
        return None
    return _find_session_id(parsed)


def _extract_transport_session_id_from_line(line: str) -> str | None:
    try:
        parsed = cast("object", json.loads(line))
    except json.JSONDecodeError:
        return _match_transport_text_session_id(line.strip())
    if not isinstance(parsed, dict):
        return None
    return _match_transport_json_session_id(parsed)


def extract_session_id(raw_output: list[str] | tuple[str, ...]) -> str | None:
    """Extract a nested session identifier from raw NDJSON output lines."""
    for line in raw_output:
        session_id = _extract_session_id_from_line(line)
        if session_id:
            return session_id
    return None


def extract_transport_session_id(raw_output: list[str] | tuple[str, ...]) -> str | None:
    """Extract only top-level transport/runtime session IDs from output lines."""
    for line in raw_output:
        session_id = _extract_transport_session_id_from_line(line)
        if session_id:
            return session_id
    return None


def extract_transport_session_id_from_line(line: str) -> str | None:
    """Extract only top-level transport/runtime session IDs from one line."""
    return _extract_transport_session_id_from_line(line)


def extract_visible_tui_transport_session_id(text: str) -> str | None:
    """Extract transport session IDs from visible TUI text only.

    This intentionally excludes generic ``session_id=...`` patterns so assistant or
    tool text cannot masquerade as transport session metadata.
    """
    return _match_transport_text_session_id(text.strip())


def _bounded_output_lines(
    raw_output: list[str] | tuple[str, ...],
    *,
    explicit_completion_seen: bool = False,
) -> list[str]:
    lines = list(raw_output)
    if explicit_completion_seen and not any(_EXPLICIT_COMPLETION_MARKER in line for line in lines):
        lines.append(_EXPLICIT_COMPLETION_MARKER)
    return lines
