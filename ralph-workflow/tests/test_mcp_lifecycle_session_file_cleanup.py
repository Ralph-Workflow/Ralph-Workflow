from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle


def test_session_metadata_uses_system_temporary_storage_outside_workspace(tmp_path: Path) -> None:
    """S-3 regression: transient MCP metadata never creates a workspace-tree file."""
    session = AgentSession(session_id="session", run_id="run", drain="planning", capabilities=set())

    session_file = lifecycle._create_session_file(tmp_path, session)

    try:
        assert not session_file.is_relative_to(tmp_path)
        assert session_file.read_text(encoding="utf-8") == lifecycle.session_payload_json(session)
    finally:
        session_file.unlink(missing_ok=True)


def test_spawn_failure_removes_created_session_file(tmp_path: Path) -> None:
    def fail_spawn(
        _command: list[str], _cwd: Path, _env: dict[str, str], *, phase: str | None = None
    ) -> object:
        del phase
        raise RuntimeError("spawn failed")

    deps = lifecycle.LifecycleDeps(
        reserve_port=lambda: 1,
        create_session_file=lifecycle._create_session_file,
        subprocess_env=lambda _session_file: {},
        spawn_process=fail_spawn,
        preflight=lambda _endpoint, _tools, _timeout: None,
        preflight_timeout=lambda: timedelta(seconds=1),
    )
    session = AgentSession(session_id="session", run_id="run", drain="planning", capabilities=set())

    with pytest.raises(RuntimeError, match="spawn failed"):
        lifecycle._spawn_mcp_process(tmp_path, session, deps, None, None, [], port=43123)

    assert list((tmp_path / ".agent").glob("ralph-mcp-session-*.json")) == []
