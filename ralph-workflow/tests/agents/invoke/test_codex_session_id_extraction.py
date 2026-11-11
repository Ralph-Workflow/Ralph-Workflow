from __future__ import annotations

import json

from ralph.agents.invoke._session import (
    extract_transport_session_id,
    extract_transport_session_id_from_line,
)


def test_thread_started_frame_is_the_transport_session_id() -> None:
    line = json.dumps(
        {"type": "thread.started", "thread_id": "01a054e7-35e4-7dc0-87a3-2e977c4d9a48"}
    )

    assert extract_transport_session_id_from_line(line) == "01a054e7-35e4-7dc0-87a3-2e977c4d9a48"
    assert extract_transport_session_id([line]) == "01a054e7-35e4-7dc0-87a3-2e977c4d9a48"


def test_thread_started_is_found_ahead_of_the_turn_frames() -> None:
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "codex-thread-456"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "error"}}),
    ]

    assert extract_transport_session_id(lines) == "codex-thread-456"


def test_a_non_session_frame_carrying_thread_id_is_not_a_session_frame() -> None:
    line = json.dumps({"type": "item.completed", "thread_id": "spoofed-id"})

    assert extract_transport_session_id_from_line(line) is None


def test_thread_started_with_empty_thread_id_yields_none() -> None:
    line = json.dumps({"type": "thread.started", "thread_id": ""})

    assert extract_transport_session_id_from_line(line) is None
