from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from typing_extensions import get_protocol_members

from ralph.mcp.protocol.session import AgentSession, McpSession
from ralph.mcp.server.runtime_session import FileBackedSession

if TYPE_CHECKING:
    from pathlib import Path

_SENTINEL = object()


def _make_file_backed_session(tmp_path: Path) -> FileBackedSession:
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


def _agent_session_public_surface() -> set[str]:
    instance = AgentSession(session_id="s", run_id="r", drain="d")
    names = {name for name in dir(AgentSession) if not name.startswith("_")}
    names |= {name for name in vars(instance) if not name.startswith("_")}
    return names


def test_file_backed_session_provides_full_agent_session_surface(tmp_path: Path) -> None:
    session = _make_file_backed_session(tmp_path)
    missing = sorted(
        name
        for name in _agent_session_public_surface()
        if getattr(session, name, _SENTINEL) is _SENTINEL
    )
    assert not missing, (
        "FileBackedSession is missing AgentSession public members "
        f"(production AttributeError risk): {missing}"
    )


def test_agent_session_surface_derivation_is_not_empty() -> None:
    surface = _agent_session_public_surface()
    assert {
        "session_id",
        "capabilities",
        "tool_output_sink_entry",
        "current_thread_tool_output_sink",
    } <= surface


def test_file_backed_session_supports_the_sink_swap_contract(tmp_path: Path) -> None:
    session = _make_file_backed_session(tmp_path)
    events: list[dict[str, object]] = []

    session.tool_output_sink_entry = (threading.get_ident(), events.append)
    sink = session.current_thread_tool_output_sink()
    assert sink is not None
    sink({"tool": "exec", "text": "hi"})
    assert events == [{"tool": "exec", "text": "hi"}]

    session.tool_output_sink_entry = None
    assert session.current_thread_tool_output_sink() is None


def test_both_session_implementations_satisfy_mcp_session_protocol(tmp_path: Path) -> None:
    file_backed: McpSession = _make_file_backed_session(tmp_path)
    in_memory: McpSession = AgentSession(session_id="s", run_id="r", drain="d")
    assert file_backed.session_id == "sess-1"
    assert in_memory.session_id == "s"


def test_mcp_session_protocol_covers_full_agent_session_surface() -> None:
    protocol_members = get_protocol_members(McpSession)
    missing = sorted(_agent_session_public_surface() - set(protocol_members))
    assert not missing, f"McpSession protocol is stale; missing members: {missing}"
