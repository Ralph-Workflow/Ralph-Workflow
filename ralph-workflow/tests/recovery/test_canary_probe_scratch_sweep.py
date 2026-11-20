from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.agent_config import AgentConfig
from ralph.recovery.canary_probe import (
    CANARY_MARKER_CONTENT,
    CANARY_MARKER_FILENAME,
    PROBE_OWNER_PID_FILENAME,
    ProbeOutcome,
    run_canary_probe,
    sweep_canary_probe_scratch_workspaces,
)
from ralph.workspace.agent_dir_retention import sweep_agent_dir

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions

_NOW = 1_000_000_000.0
_WEEK = 7 * 24 * 3600.0
_CUTOFF = _NOW - _WEEK


def _probe_area(tmp_path: Path) -> Path:
    area = tmp_path / ".agent" / "tmp"
    area.mkdir(parents=True, exist_ok=True)
    return area


def _make_scratch(area: Path, name: str) -> Path:
    scratch = area / name
    scratch.mkdir()
    return scratch


def _write_owner_pid(scratch: Path, pid: int) -> None:
    (scratch / PROBE_OWNER_PID_FILENAME).write_text(str(pid), encoding="utf-8")


def _find_dead_pid() -> int:
    for candidate in range(4_000_000, 3_900_000, -1):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except PermissionError:
            continue
        except OSError:
            continue
    raise AssertionError("no dead process id was available for the owner-liveness check")


def _assert_dead(pid: int) -> None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    raise AssertionError(f"process {pid} was expected to be dead")


def test_owner_pid_marker_file_name_is_owner_pid() -> None:
    assert PROBE_OWNER_PID_FILENAME == "owner_pid"


def test_sweep_removes_probe_scratch_owned_by_a_dead_process(tmp_path: Path) -> None:
    area = _probe_area(tmp_path)
    dead_pid = _find_dead_pid()
    _assert_dead(dead_pid)
    scratch = _make_scratch(area, "canary-probe-dead-owner")
    _write_owner_pid(scratch, dead_pid)

    sweep_canary_probe_scratch_workspaces(area, cutoff=_CUTOFF)

    assert not scratch.exists()


def test_sweep_keeps_probe_scratch_owned_by_a_live_process(tmp_path: Path) -> None:
    area = _probe_area(tmp_path)
    scratch = _make_scratch(area, "canary-probe-live-owner")
    _write_owner_pid(scratch, os.getpid())

    sweep_canary_probe_scratch_workspaces(area, cutoff=_CUTOFF)

    assert scratch.exists()
    assert (scratch / PROBE_OWNER_PID_FILENAME).exists()


def test_sweep_keeps_recent_markerless_probe_scratch_and_removes_aged_markerless(
    tmp_path: Path,
) -> None:
    area = _probe_area(tmp_path)
    recent = _make_scratch(area, "canary-probe-recent")
    aged = _make_scratch(area, "canary-probe-aged")
    os.utime(recent, (_NOW, _NOW))
    os.utime(aged, (_NOW - _WEEK - 60.0, _NOW - _WEEK - 60.0))

    sweep_canary_probe_scratch_workspaces(area, cutoff=_CUTOFF)

    assert recent.exists()
    assert not aged.exists()


def test_run_start_retention_reclaims_probe_scratch_of_a_dead_owner(tmp_path: Path) -> None:
    area = _probe_area(tmp_path)
    dead_pid = _find_dead_pid()
    _assert_dead(dead_pid)
    scratch = _make_scratch(area, "canary-probe-dead-owner")
    _write_owner_pid(scratch, dead_pid)

    sweep_agent_dir(tmp_path, keep_run_id="current", now=lambda: _NOW)

    assert not scratch.exists()


def test_production_scratch_records_the_owner_pid_marker(tmp_path: Path) -> None:
    trusted_parent = tmp_path / "trusted"
    trusted_parent.mkdir()
    observed_paths: list[Path | None] = []
    observed_entries: list[str] = []
    observed_marker_texts: list[str] = []

    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str]:
        workspace_path = options.workspace_path if options is not None else None
        observed_paths.append(workspace_path)
        if workspace_path is None:
            return iter(())
        observed_entries.extend(entry.name for entry in workspace_path.iterdir())
        marker_path = workspace_path / "owner_pid"
        observed_marker_texts.append(
            marker_path.read_text(encoding="utf-8") if marker_path.is_file() else ""
        )
        (workspace_path / CANARY_MARKER_FILENAME).write_text(
            CANARY_MARKER_CONTENT, encoding="utf-8"
        )
        return iter(())

    result = run_canary_probe(
        AgentConfig(cmd="fake-agent", model="fake-model"),
        invoke_fn=_invoke,
        trusted_parent=trusted_parent,
    )

    assert result.outcome == ProbeOutcome.PASSED
    assert len(observed_paths) == 1
    scratch = observed_paths[0]
    assert scratch is not None
    assert scratch.parent == trusted_parent / ".agent" / "tmp"
    assert scratch.name.startswith("canary-probe-")
    assert "owner_pid" in observed_entries
    assert len(observed_marker_texts) == 1
    assert observed_marker_texts[0] == str(os.getpid())
    assert int(observed_marker_texts[0]) == os.getpid()
