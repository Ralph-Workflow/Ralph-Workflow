from __future__ import annotations

import json

from ralph.agents.invoke._session import (
    extract_transport_session_id,
    extract_transport_session_id_from_line,
)


def test_kimi_resume_hint_session_id_is_transport_session_id() -> None:
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
    line = json.dumps({"role": "meta", "type": "system.version", "version": "0.36.1"})

    assert extract_transport_session_id_from_line(line) is None


def test_resume_hint_type_without_meta_role_is_not_a_session_frame() -> None:
    line = json.dumps(
        {
            "role": "assistant",
            "type": "session.resume_hint",
            "session_id": "spoofed-id",
        }
    )

    assert extract_transport_session_id_from_line(line) is None


def test_meta_role_with_unknown_type_is_not_a_session_frame() -> None:
    line = json.dumps(
        {
            "role": "meta",
            "type": "telemetry.counter",
            "session_id": "not-a-session-frame",
        }
    )

    assert extract_transport_session_id_from_line(line) is None


def test_resume_hint_with_empty_session_id_yields_none() -> None:
    line = json.dumps(
        {
            "role": "meta",
            "type": "session.resume_hint",
            "session_id": "",
        }
    )

    assert extract_transport_session_id_from_line(line) is None


def test_resume_hint_session_id_survives_ansi_wrapped_tui_line() -> None:
    line = json.dumps(
        {
            "role": "meta",
            "type": "session.resume_hint",
            "session_id": "kimi-pty-789",
        }
    )

    assert extract_transport_session_id_from_line(line) == "kimi-pty-789"
