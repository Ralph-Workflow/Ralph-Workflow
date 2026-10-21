"""Installer children must not inherit Python import-injection variables.

``make install`` exports ``PYTHONPATH`` at the source checkout. If that leaks
into the snapshot's ``ralph --version`` child, the child imports the unflavored
source package instead of the snapshot and the install fails validation.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import install as install_module
from ralph.executor import process as executor_process
from ralph.process._spawn_env import INSTALLER_PYTHON_INJECTION_VARS
from ralph.process.manager import ProcessManager, ProcessManagerPolicy, SpawnOptions
from ralph.testing._process_state import ProcessState
from ralph.testing.fake_process import FakePopen

if TYPE_CHECKING:
    from collections.abc import Callable

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


class _UvVersionPopen(FakePopen):
    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes | None, bytes | None]:
        del input, timeout
        return b"uv 0.8.0\n", b""


def _install_recording_pm(
    monkeypatch: pytest.MonkeyPatch, spawned_envs: list[dict[str, str] | None]
) -> None:
    pid_iter = itertools.count(1)

    def factory(command: object, opts: SpawnOptions) -> object:
        del command
        spawned_envs.append(opts.env)
        return _UvVersionPopen(next(pid_iter), state=ProcessState(returncode=0))

    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=factory)
    monkeypatch.setattr(executor_process, "get_process_manager", lambda: pm)


_INSTALLER_SPAWNS: dict[str, Callable[[], object]] = {
    "run_command": lambda: install_module.run_command(("uv", "sync"), cwd=Path("/snapshot")),
    "preflight_uv": lambda: install_module.preflight_uv("uv"),
    "run_snapshot_version": lambda: install_module.run_snapshot_version("uv", Path("/snapshot")),
}


@pytest.mark.parametrize("spawn", sorted(_INSTALLER_SPAWNS))
def test_installer_child_does_not_inherit_python_injection_variables(
    monkeypatch: pytest.MonkeyPatch, spawn: str
) -> None:
    for name in INSTALLER_PYTHON_INJECTION_VARS:
        monkeypatch.setenv(name, "/source/checkout")
    monkeypatch.setenv("UV_INDEX_URL", "https://index.example")
    spawned_envs: list[dict[str, str] | None] = []
    _install_recording_pm(monkeypatch, spawned_envs)

    _INSTALLER_SPAWNS[spawn]()

    [child_env] = spawned_envs
    assert child_env is not None
    assert not INSTALLER_PYTHON_INJECTION_VARS.intersection(child_env)
    assert child_env["UV_INDEX_URL"] == "https://index.example"


def _executable(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_global_executable_skips_the_installers_own_environment(tmp_path: Path) -> None:
    """``uv run`` puts the checkout's ``.venv/bin`` first; it is not a global install."""
    own_bin = tmp_path / "checkout" / ".venv" / "bin"
    _executable(own_bin, "ralph")
    global_ralph = _executable(tmp_path / "home" / ".local" / "bin", "ralph")
    search_path = f"{own_bin}:{global_ralph.parent}"

    found = install_module.global_executable("ralph", search_path=search_path, own_bin=own_bin)

    assert found == str(global_ralph)


def test_global_executable_reports_nothing_when_only_the_own_environment_has_it(
    tmp_path: Path,
) -> None:
    own_bin = tmp_path / ".venv" / "bin"
    _executable(own_bin, "ralph")

    assert (
        install_module.global_executable("ralph", search_path=str(own_bin), own_bin=own_bin) is None
    )
