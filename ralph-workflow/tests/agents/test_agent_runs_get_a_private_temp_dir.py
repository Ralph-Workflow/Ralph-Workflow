from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke.agent_temp_dir import private_agent_tmpdir

if TYPE_CHECKING:
    from pathlib import Path


def test_everything_an_agent_run_leaves_in_its_temp_dir_is_removed(tmp_path: Path) -> None:
    with private_agent_tmpdir(root=tmp_path) as tmpdir:
        (tmpdir / "unitas_roll_a_ball_project_open").mkdir()
        (tmpdir / "unitas_roll_a_ball_project_open" / "scene.json").write_text("{}")
        os.mkfifo(tmpdir / "clr-debug-pipe-1234-out")
        assert tmpdir.parent == tmp_path
    assert not tmpdir.exists()


def test_the_private_temp_dir_leaves_room_for_unix_socket_names() -> None:
    with private_agent_tmpdir() as tmpdir:
        socket_path = tmpdir / "dotnet-diagnostic-99999-1795723291-socket"
        assert len(str(socket_path)) < 104


def test_a_failed_run_still_cleans_up(tmp_path: Path) -> None:
    seen: list[Path] = []

    def crash_inside_the_run() -> None:
        with private_agent_tmpdir(root=tmp_path) as tmpdir:
            seen.append(tmpdir)
            (tmpdir / "partial").write_text("x")
            raise RuntimeError("agent crashed")

    with pytest.raises(RuntimeError, match="agent crashed"):
        crash_inside_the_run()
    assert not seen[0].exists()
