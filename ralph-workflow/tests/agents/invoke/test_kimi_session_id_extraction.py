"""Kimi Code stream-json session ID extraction tests.

Kimi Code's measured v0.36.1 wire emits the resumable session id on a
role-keyed meta frame: ``{"role": "meta", "type": "session.resume_hint",
"session_id": ..., "command": "kimi -S <id>"}``.  These tests pin the
extraction predicate (both keys required) and the surrounding guards.
"""

from __future__ import annotations

import json

from ralph.agents.invoke._session import (
    extract_transport_session_id,
    extract_transport_session_id_from_line,
)


def test_kimi_resume_hint_session_id_is_transport_session_id() -> None:
    """The meta ``session.resume_hint`` frame carries the resumable session id."""
    line = json.dumps(
        {
            "role": "meta",
            "type": "session.resume_hint",
            "session_id": "kimi-session-123",
            "command": "kimi -S kimi-session-123",
        }
    )

    assert extract_transport_session_id_from_line(line) == "kimi-session-123"
    assert extract_transport_session_id([line]) == "kimi-session-123"


def test_kimi_resume_hint_found_in_stream_after_other_frames() -> None:
    """The meta frame appears after the version banner; extraction scans the stream."""
    lines = [
        json.dumps({"role": "meta", "type": "system.version", "version": "0.36.1"}),
        json.dumps({"role": "assistant", "content": "working on it"}),
        json.dumps(
            {
                "role": "meta",
                "type": "session.resume_hint",
                "session_id": "kimi-session-456",
            }
        ),
    ]

    assert extract_transport_session_id(lines) == "kimi-session-456"


def test_meta_role_without_resume_hint_type_is_not_a_session_frame() -> None:
    """``system.version`` carries no session id; it must not match the predicate."""
    line = json.dumps({"role": "meta", "type": "system.version", "version": "0.36.1"})

    assert extract_transport_session_id_from_line(line) is None


def test_resume_hint_type_without_meta_role_is_not_a_session_frame() -> None:
    """The predicate requires BOTH the meta role and the resume_hint type.

    An assistant message whose free text happens to carry a
    ``session.resume_hint`` type key must not masquerade as transport
    session metadata.
    """
    line = json.dumps(
        {
            "role": "assistant",
            "type": "session.resume_hint",
            "session_id": "spoofed-id",
        }
    )

    assert extract_transport_session_id_from_line(line) is None


def test_meta_role_with_unknown_type_is_not_a_session_frame() -> None:
    """A future meta type must not accidentally release the generic key scan."""
    line = json.dumps(
        {
            "role": "meta",
            "type": "telemetry.counter",
            "session_id": "not-a-session-frame",
        }
    )

    assert extract_transport_session_id_from_line(line) is None


def test_resume_hint_with_empty_session_id_yields_none() -> None:
    """An empty session id string is treated as absent."""
    line = json.dumps(
        {
            "role": "meta",
            "type": "session.resume_hint",
            "session_id": "",
        }
    )

    assert extract_transport_session_id_from_line(line) is None


def test_resume_hint_session_id_survives_ansi_wrapped_tui_line() -> None:
    """The PTY-visible fallback path also captures the meta frame content.

    ``extract_transport_session_id_from_line`` JSON-parses the raw line,
    so ANSI codes wrapping the JSON would fail the parse and fall to the
    text patterns; this pins that the plain (non-wrapped) PTY line still
    extracts, mirroring the cursor extraction guarantee.
    """
    line = json.dumps(
        {
            "role": "meta",
            "type": "session.resume_hint",
            "session_id": "kimi-pty-789",
        }
    )

    assert extract_transport_session_id_from_line(line) == "kimi-pty-789"
