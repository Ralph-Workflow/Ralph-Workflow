from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from ralph.mcp.server.start_stage_files import (
    discard_stage_files,
    record,
    record_overrun,
    stage_file_path,
    sweep_stale_stage_files,
)

if TYPE_CHECKING:
    from pathlib import Path


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_a_finished_spawn_leaves_no_stage_files_behind(tmp_path: Path) -> None:
    record(tmp_path, "ready_to_serve")
    record_overrun(tmp_path, "config_loaded", 1.0, 0.5, 0.0)
    own = stage_file_path(tmp_path)
    assert own.exists()

    discard_stage_files(tmp_path, spawn_id=own.name.rsplit(".", 1)[-1])

    assert list((tmp_path / ".agent").iterdir()) == []


def test_a_new_spawn_sweeps_stage_files_left_by_old_spawns(tmp_path: Path) -> None:
    agent = tmp_path / ".agent"
    agent.mkdir()
    leaked = [agent / f".ralph_mcp_start_stage.old-{n}" for n in range(50)]
    for path in leaked:
        path.write_text("ready_to_serve", encoding="ascii")
        _age(path, 2 * 3600)
    leaked_overruns = agent / ".ralph_mcp_start_stage.old-0.overruns"
    leaked_overruns.write_text("{}\n", encoding="utf-8")
    _age(leaked_overruns, 2 * 3600)
    starting = agent / ".ralph_mcp_start_stage.concurrent-spawn"
    starting.write_text("config_loading", encoding="ascii")
    unrelated = agent / "PLAN.md"
    unrelated.write_text("plan", encoding="utf-8")
    _age(unrelated, 2 * 3600)

    removed = sweep_stale_stage_files(tmp_path, max_age_s=3600.0)

    assert removed == 51
    assert sorted(p.name for p in agent.iterdir()) == [
        ".ralph_mcp_start_stage.concurrent-spawn",
        "PLAN.md",
    ]


def test_sweeping_a_workspace_without_an_agent_dir_is_a_no_op(tmp_path: Path) -> None:
    assert sweep_stale_stage_files(tmp_path, max_age_s=3600.0) == 0
