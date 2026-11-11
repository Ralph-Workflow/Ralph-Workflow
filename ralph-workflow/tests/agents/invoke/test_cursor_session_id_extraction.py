from __future__ import annotations

import json

from ralph.agents.invoke._session import (
    extract_transport_session_id,
    extract_transport_session_id_from_line,
)


def test_cursor_system_init_session_id_is_transport_session_id() -> None:
    line = json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "session_id": "cursor-session-123",
            "model": "Auto",
        }
    )

    assert extract_transport_session_id_from_line(line) == "cursor-session-123"
    assert extract_transport_session_id([line]) == "cursor-session-123"
