from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


def test_mcp_server_starts_in_a_workspace_holding_a_conflicted_ralph_package(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    conflicted_pkg = workspace / "ralph"
    conflicted_pkg.mkdir(parents=True)
    (conflicted_pkg / "__init__.py").write_text(
        "<<<<<<< HEAD\n=======\n>>>>>>> 42d31a5dd (conflicted checkout)\n",
        encoding="utf-8",
    )
    session = AgentSession(
        session_id=str(uuid.uuid4()),
        run_id="run-conflicted-workspace",
        drain="standalone",
    )

    bridge = lifecycle.start_mcp_server(session, FsWorkspace(workspace))
    try:
        lifecycle.check_mcp_bridge_health(bridge)
    finally:
        lifecycle.shutdown_mcp_server(bridge)
