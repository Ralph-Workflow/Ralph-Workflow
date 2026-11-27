from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.subprocess_e2e


@pytest.mark.timeout_seconds(60)
def test_starting_a_server_clears_stage_files_leaked_by_old_spawns(tmp_path: Path) -> None:
    agent = tmp_path / ".agent"
    agent.mkdir()
    an_hour_and_more_ago = time.time() - 2 * 3600
    for n in range(40):
        leaked = agent / f".ralph_mcp_start_stage.leaked-{n}"
        leaked.write_text("ready_to_serve", encoding="ascii")
        os.utime(leaked, (an_hour_and_more_ago, an_hour_and_more_ago))

    session = AgentSession(
        session_id="test-bounded-stage-files",
        run_id="run-bounded-stage-files",
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    bridge = lifecycle.start_mcp_server(session, FsWorkspace(tmp_path))
    try:
        remaining = [p.name for p in agent.glob(".ralph_mcp_start_stage.*")]
        assert not [name for name in remaining if "leaked-" in name]
        assert len(remaining) <= 2
    finally:
        bridge.shutdown()
