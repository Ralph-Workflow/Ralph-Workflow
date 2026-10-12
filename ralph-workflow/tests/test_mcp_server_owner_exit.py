"""Regression tests for MCP servers outliving the Ralph process that spawned them.

An owner killed without its cleanup (SIGKILL, a crashed pytest-xdist worker)
used to leave ``python -m ralph.mcp.server`` running indefinitely; dozens of
such orphans were found on a long-lived host.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.protocol.env import MCP_OWNER_PID_ENV
from ralph.mcp.server import lifecycle
from ralph.mcp.server.runtime import exit_when_owner_exits


def test_lifecycle_names_itself_as_the_server_owner(tmp_path: Path) -> None:
    """The spawned server learns which process owns it."""
    env = lifecycle._subprocess_env(tmp_path / "session.json")

    assert env[MCP_OWNER_PID_ENV] == str(lifecycle.os.getpid())


def test_server_exits_once_reparented_away_from_its_owner() -> None:
    """The watcher exits the server when its parent is no longer the owner."""
    parents = iter([100, 100, 100, 1])
    exits: list[int] = []
    env = {MCP_OWNER_PID_ENV: "100"}

    watcher = exit_when_owner_exits(
        env,
        get_parent_pid=lambda: next(parents),
        sleep=lambda _seconds: None,
        exit_process=exits.append,
    )

    assert watcher is not None
    watcher.join(timeout=5)
    assert exits == [0]
    assert MCP_OWNER_PID_ENV not in env


def test_standalone_server_without_owner_is_not_watched() -> None:
    """``ralph-mcp`` run by hand has no owner and keeps running."""
    assert exit_when_owner_exits({}, get_parent_pid=lambda: 1) is None


def test_owner_that_is_not_the_parent_is_ignored() -> None:
    """A stale or forwarded owner PID never arms the watcher."""
    env = {MCP_OWNER_PID_ENV: "100"}

    assert exit_when_owner_exits(env, get_parent_pid=lambda: 200) is None
    assert MCP_OWNER_PID_ENV not in env
