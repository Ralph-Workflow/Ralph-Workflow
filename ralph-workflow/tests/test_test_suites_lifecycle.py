"""Lifecycle and timeout tests for the maintained test-suite runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ralph import test_suites as test_suites_module

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pytest


class _FakeShardProcess:
    def __init__(
        self,
        returncodes: list[int | None],
        *,
        stderr: bytes = b"",
        communicate_times_out: bool = False,
    ) -> None:
        self._returncodes = list(returncodes)
        self._last_returncode: int | None = None
        self._stderr = stderr
        self._communicate_times_out = communicate_times_out
        self.terminated = False
        self.reaped = False
        self.orphans_cleaned = False

    def poll(self) -> int | None:
        if self._returncodes:
            self._last_returncode = self._returncodes.pop(0)
        return self._last_returncode

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input, timeout
        if self._communicate_times_out:
            raise subprocess.TimeoutExpired(("pytest",), 1.0)
        self.reaped = True
        return b"", self._stderr

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True
        self._last_returncode = -15

    def cleanup_orphans(self) -> None:
        self.orphans_cleaned = True


class _StubSpawner:
    def __init__(self, processes: list[_FakeShardProcess]) -> None:
        self._processes = list(processes)
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> _FakeShardProcess:
        self.calls.append((tuple(command), cwd, dict(env)))
        return self._processes.pop(0)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_run_test_suites_terminates_and_reaps_siblings_on_first_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py", "tests/test_bravo.py"),
    )
    failed = _FakeShardProcess([1], stderr=b"failed\n")
    sibling = _FakeShardProcess([None])
    spawner = _StubSpawner([failed, sibling])

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_discoverer=lambda _cwd: (
            "tests/test_alpha.py",
            "tests/test_bravo.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 1
    assert failed.reaped
    assert not failed.terminated
    assert sibling.terminated
    assert sibling.reaped


def test_run_test_suites_uses_one_parent_deadline_and_reaps_all_on_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py", "tests/test_bravo.py"),
    )
    processes = [_FakeShardProcess([None]), _FakeShardProcess([None])]
    spawner = _StubSpawner(processes)
    clock = _FakeClock()

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=5.0,
        spawner=spawner,
        file_discoverer=lambda _cwd: (
            "tests/test_alpha.py",
            "tests/test_bravo.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    assert all(process.terminated and process.reaped for process in processes)


def test_run_test_suites_spawn_deadline_names_already_started_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py", "tests/test_bravo.py"),
    )
    clock = _FakeClock()
    process = _FakeShardProcess([None])

    def spawn_after_deadline(
        command: Sequence[str], *, cwd: Path, env: Mapping[str, str]
    ) -> _FakeShardProcess:
        del command, cwd, env
        clock.advance(5.0)
        return process

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=5.0,
        spawner=spawn_after_deadline,
        file_discoverer=lambda _cwd: ("tests/test_alpha.py", "tests/test_bravo.py"),
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    assert process.terminated and process.reaped
    assert capsys.readouterr().err.splitlines()[:1] == [
        "pytest shard 0 timed out after 5.00s; files: tests/test_alpha.py",
    ]


def test_run_test_suites_timeout_names_each_incomplete_shard_and_its_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py", "tests/test_bravo.py"),
    )
    clock = _FakeClock()

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=5.0,
        spawner=_StubSpawner([_FakeShardProcess([None]), _FakeShardProcess([None])]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py", "tests/test_bravo.py"),
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    assert capsys.readouterr().err.splitlines()[:2] == [
        "pytest shard 0 timed out after 5.00s; files: tests/test_alpha.py",
        "pytest shard 1 timed out after 5.00s; files: tests/test_bravo.py",
    ]


def test_timeout_regression_deadline_without_drain_does_not_claim_shard_survived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A zero-budget deadline cannot honestly diagnose a failed post-termination drain."""
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    clock = _FakeClock()
    process = _FakeShardProcess([None], communicate_times_out=True)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=1.0,
        spawner=_StubSpawner([process]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    assert process.terminated
    assert "pytest shard did not exit after termination" not in capsys.readouterr().err


def test_completed_shard_cleans_descendants_before_draining_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    process = _FakeShardProcess([0])

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=_StubSpawner([process]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    assert process.reaped
    assert process.orphans_cleaned


def test_pipe_drain_timeout_still_cleans_shard_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    process = _FakeShardProcess([0], communicate_times_out=True)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=_StubSpawner([process]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    assert not process.reaped
    assert process.orphans_cleaned


def test_run_test_suites_charges_static_discovery_to_parent_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    clock = _FakeClock()
    spawner = _StubSpawner([])

    def discover(_cwd: Path) -> tuple[str, ...]:
        clock.advance(5.0)
        return ("tests/test_alpha.py",)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=5.0,
        spawner=spawner,
        file_discoverer=discover,
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    assert spawner.calls == []
