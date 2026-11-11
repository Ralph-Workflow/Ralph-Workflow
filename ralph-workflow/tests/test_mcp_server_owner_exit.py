from __future__ import annotations

from pathlib import Path

from ralph.mcp.protocol.env import MCP_OWNER_PID_ENV
from ralph.mcp.server import lifecycle
from ralph.mcp.server.runtime import exit_when_owner_exits


def test_lifecycle_names_itself_as_the_server_owner(tmp_path: Path) -> None:
    env = lifecycle._subprocess_env(tmp_path / "session.json")

    assert env[MCP_OWNER_PID_ENV] == str(lifecycle.os.getpid())


def test_server_exits_once_reparented_away_from_its_owner() -> None:
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
    assert exit_when_owner_exits({}, get_parent_pid=lambda: 1) is None


def test_server_exits_when_owner_died_before_watcher_started() -> None:
    env = {MCP_OWNER_PID_ENV: "100"}
    exits: list[int] = []

    assert (
        exit_when_owner_exits(
            env,
            get_parent_pid=lambda: 200,
            exit_process=exits.append,
        )
        is None
    )
    assert exits == [0]
    assert MCP_OWNER_PID_ENV not in env
