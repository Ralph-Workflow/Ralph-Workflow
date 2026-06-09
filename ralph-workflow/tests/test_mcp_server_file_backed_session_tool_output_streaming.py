"""Regression test: FileBackedSession must support the tool-output streaming surface.

The standalone MCP server runs with a ``FileBackedSession`` (built by
``session_from_env`` from ``MCP_SESSION_FILE``). The exec SSE streaming path
installs an atomic (owner thread, sink) entry on the session and the exec
handler captures it via ``current_thread_tool_output_sink``. ``AgentSession``
provides this surface; ``FileBackedSession`` originally did not — the first
production exec call raised ``AttributeError`` after the SSE headers were
sent, closing the socket with zero frames and leaving the MCP client to hang
until its request timeout (the ``-32001 Request timed out`` retry storm).
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from ralph.mcp.server.runtime_session import FileBackedSession

if TYPE_CHECKING:
    from pathlib import Path


def _make_session(tmp_path: Path) -> FileBackedSession:
    session_file = tmp_path / "session.json"
    session_file.write_text(
        json.dumps(
            {
                "session_id": "sess-1",
                "run_id": "run-1",
                "drain": "standalone",
                "capabilities": ["ProcessExecBounded"],
            }
        ),
        encoding="utf-8",
    )
    return FileBackedSession(session_file)


def test_sink_entry_defaults_to_none(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    assert session.tool_output_sink_entry is None
    assert session.current_thread_tool_output_sink() is None


def test_owning_thread_sees_its_sink(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    events: list[dict[str, object]] = []
    session.tool_output_sink_entry = (threading.get_ident(), events.append)

    sink = session.current_thread_tool_output_sink()
    assert sink is not None
    sink({"tool": "exec", "stream": "combined", "text": "hi"})
    assert events == [{"tool": "exec", "stream": "combined", "text": "hi"}]


def test_non_owning_thread_sees_no_sink(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    session.tool_output_sink_entry = (
        threading.get_ident() + 1,
        lambda _event: None,
    )
    assert session.current_thread_tool_output_sink() is None
