from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

from ralph.mcp.server.lifecycle import _spawn_process

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager import ManagedProcess, SpawnOptions


def test_spawned_mcp_server_output_lands_in_agent_tmp_log(tmp_path: Path) -> None:
    captured: dict[str, SpawnOptions] = {}

    def fake_spawn(command: object, options: SpawnOptions) -> ManagedProcess:
        captured["options"] = options
        assert isinstance(options.stderr, int)
        os.write(options.stderr, b"Traceback: boom\n")
        return object()

    _spawn_process(
        ["python", "-m", "ralph.mcp.server"],
        tmp_path,
        {},
        phase="planning",
        spawn=fake_spawn,
    )

    options = captured["options"]
    assert options.stderr != subprocess.DEVNULL
    assert options.stdout == options.stderr, "stdout must share the persisted log"

    log_path = tmp_path / ".agent" / "tmp" / "mcp-server.log"
    assert log_path.exists(), "server output log must exist under .agent/tmp"
    assert "Traceback: boom" in log_path.read_text(encoding="utf-8")


def test_spawned_mcp_server_log_fd_is_released_in_parent(tmp_path: Path) -> None:
    seen_fd: list[int] = []

    def fake_spawn(command: object, options: SpawnOptions) -> ManagedProcess:
        assert isinstance(options.stderr, int)
        seen_fd.append(options.stderr)
        return object()

    _spawn_process(["x"], tmp_path, {}, phase=None, spawn=fake_spawn)

    fd = seen_fd[0]
    try:
        os.fstat(fd)
    except OSError:
        return
    raise AssertionError("parent kept the server log fd open after spawn")
