"""Session ID extraction and bounded output utilities."""

from __future__ import annotations

import json
import re
from typing import cast

from ralph.agents.invoke._pty_helpers import _visible_tui_text
from ralph.display.raw_overflow import TURN_BOUNDARY_MARKER

_EXPLICIT_COMPLETION_MARKER = "Task declared complete:"
_TURN_BOUNDARY_MARKER = TURN_BOUNDARY_MARKER

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


def _is_cursor_system_init_event(parsed: dict[str, object]) -> bool:
    return parsed.get("type") == "system" and parsed.get("subtype") == "init"


def _is_kimi_meta_session_frame(parsed: dict[str, object]) -> bool:
    """Match Kimi Code's role-keyed ``session.resume_hint`` meta frame.

    Kimi Code's NDJSON frames are keyed by ``role`` rather than ``type``;
    the session-bearing frame observed on the live v0.36.x wire is
    ``{"role": "meta", "type": "session.resume_hint", "session_id": ...}``
    (emitted right after session start, alongside a ``system.version``
    meta frame that carries no session id).  Requiring BOTH the ``meta``
    role and the ``session.resume_hint`` type keeps assistant/tool
    frames (whose free-text content could otherwise collide with the
    generic ``session_id`` keys) from masquerading as transport
    session metadata.
    """
    return parsed.get("role") == "meta" and parsed.get("type") == "session.resume_hint"


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
    is_agy_init = parsed.get("event") == "init"
    if (
        (isinstance(event_type, str) and event_type in _TRANSPORT_JSON_TYPES)
        or _is_cursor_system_init_event(parsed)
        or is_agy_init
        or _is_kimi_meta_session_frame(parsed)
    ):
        for key in ("session_id", "sessionId", "conversation_id", "id"):
            session_id = parsed.get(key)
            if isinstance(session_id, str) and session_id:
                return session_id
    # OpenCode never emits a dedicated session frame: it stamps ``sessionID``
    # (capital I, capital D -- distinct from the ``sessionId`` above) on EVERY
    # event. Gating that spelling behind ``_TRANSPORT_JSON_TYPES`` meant the
    # session was never captured for OpenCode, and the smoke reported "session
    # ID was not observed" for a run whose every line carried one. The casing is
    # OpenCode-specific, so accepting it unconditionally cannot shadow another
    # transport's session key.
    opencode_session_id = parsed.get("sessionID")
    if isinstance(opencode_session_id, str) and opencode_session_id:
        return opencode_session_id
    return _session_id_from_nested_meta(parsed)


def _session_id_from_nested_meta(parsed: dict[str, object]) -> str | None:
    """Pull a session id out of a nested ``meta`` object."""
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
        return _match_transport_text_session_id(line.strip())
    if not isinstance(parsed, dict):
        return None
    return _match_transport_json_session_id(parsed)


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


def extract_transport_session_id_with_visible_tui(line: str) -> str | None:
    """Extract transport session IDs from a PTY line, with ANSI-strip fallback.

    PTY output lines frequently contain ANSI escape codes that prevent
    ``extract_transport_session_id_from_line`` from matching the
    anchored text patterns in :data:`_TRANSPORT_TEXT_SESSION_PATTERNS`
    (e.g. ``^Claude session ready\\. Session ID:\\s*(...)$``). The
    visible-TUI helper :func:`extract_visible_tui_transport_session_id`
    strips ANSI codes via :func:`ralph.agents.invoke._pty_helpers._visible_tui_text`
    before matching, so a TUI line like
    ``\\x1b[32mClaude session ready. Session ID: abc123\\x1b[0m``
    still yields the captured id.

    Used by the PTY watchdog / recovery paths so the resumable
    session id survives a watchdog-kill -> resume flow on the PTY
    transport. Mirrors the per-line capture already used by
    :meth:`PtyLineReader._record_transcript_session_id`.
    """
    primary = extract_transport_session_id_from_line(line)
    if primary:
        return primary
    # Fallback: strip ANSI codes and re-run the visible-TUI extractor
    # so a session id carried in a TUI banner / status line is
    # captured.
    visible_line = _visible_tui_text(line)
    if visible_line and visible_line != line.strip():
        return extract_visible_tui_transport_session_id(visible_line)
    return None


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
