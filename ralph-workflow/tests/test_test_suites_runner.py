"""Tests for the ``run_test_suites`` runner, worker count policy, and profiles."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import test_suites as test_suites_module

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES = ("tests/test_auto_integrate_end_to_end.py",)


class _FakeShardProcess:
    """Controllable fake process for runner lifecycle assertions."""

    def __init__(
        self,
        returncodes: list[int | None],
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        communicate_times_out: bool = False,
    ) -> None:
        self._returncodes = list(returncodes)
        self._last_returncode: int | None = None
        self._stdout = stdout
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
        return self._stdout, self._stderr

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True
        self._last_returncode = -15

    def cleanup_orphans(self) -> None:
        self.orphans_cleaned = True


class _StubSpawner:
    """Return predetermined fake shard processes and record their commands."""

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
    """Deterministic monotonic clock for deadline tests."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds

@pytest.mark.parametrize(
    ("cpu_count", "expected_workers"),
    ((None, "1"), (1, "1"), (2, "1"), (16, "15"), (32, "31"), (64, "32")),
)
def test_auto_worker_count_preserves_one_core_and_caps_at_thirty_two(
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    expected_workers: str,
) -> None:
    monkeypatch.delenv("PYTEST_WORKERS", raising=False)
    monkeypatch.setattr(test_suites_module.os, "cpu_count", lambda: cpu_count)

    assert test_suites_module._pytest_workers() == expected_workers


def test_explicit_worker_count_overrides_the_auto_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "3")

    assert test_suites_module._pytest_workers() == "3"


@pytest.mark.parametrize(
    ("cpu_count", "shard_count"),
    (
        (None, "1"),
        (1, "1"),
        (2, "1"),
        (16, "15"),
        (32, "16"),
        (64, "16"),
    ),
)
def test_default_xdist_workers_per_shard_is_zero(
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    shard_count: str,
) -> None:
    """Default in-shard xdist is disabled on every host profile.

    The plain-pytest per-shard path keeps the slowest shard under the
    combined 60-second budget on the maintained 32-core CI profile
    because the shard-saturated 16-shard fan-out already maximises
    pytest-process parallelism and adding in-shard xdist workers only
    shifts wall-clock budget from parallel IO into pytest-coordination
    overhead. Operators that want the legacy CPU-utilisation-maximising
    policy must opt in by setting ``PYTEST_XDIST_WORKERS_PER_SHARD=auto``
    (see ``test_auto_xdist_worker_count_respects_available_cores``).
    """
    monkeypatch.delenv("PYTEST_XDIST_WORKERS_PER_SHARD", raising=False)
    monkeypatch.setenv("PYTEST_WORKERS", shard_count)
    monkeypatch.setattr(test_suites_module.os, "cpu_count", lambda: cpu_count)

    assert test_suites_module._xdist_workers_per_shard() == "0"


@pytest.mark.parametrize(
    ("cpu_count", "shard_count", "expected_workers"),
    (
        (None, "1", "2"),
        (1, "1", "1"),
        (2, "1", "2"),
        (16, "15", "1"),
        (32, "16", "2"),
        (64, "16", "4"),
    ),
)
def test_auto_xdist_worker_count_respects_available_cores(
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    shard_count: str,
    expected_workers: str,
) -> None:
    """``PYTEST_XDIST_WORKERS_PER_SHARD=auto`` sizes in-shard fan-out from cores.

    With the env var explicitly set to ``"auto"`` the resolver sizes the
    in-shard xdist fan-out so that ``PYTEST_WORKERS`` shards * ``N``
    in-shard workers does not exceed the host's available CPU count, up
    to ``_MAX_XDIST_WORKERS_PER_SHARD``. This is the legacy
    CPU-utilisation-maximising policy and is no longer the default; see
    ``test_default_xdist_workers_per_shard_is_zero`` for the rationale.
    """
    monkeypatch.setenv("PYTEST_XDIST_WORKERS_PER_SHARD", "auto")
    monkeypatch.setenv("PYTEST_WORKERS", shard_count)
    monkeypatch.setattr(test_suites_module.os, "cpu_count", lambda: cpu_count)

    assert test_suites_module._xdist_workers_per_shard() == expected_workers


def test_explicit_xdist_worker_count_overrides_auto_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKERS_PER_SHARD", "3")

    assert test_suites_module._xdist_workers_per_shard() == "3"


def test_run_test_suites_runs_disjoint_plain_pytest_shards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setenv("PYTEST_XDIST_WORKERS_PER_SHARD", "0")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py", "tests/test_bravo.py"),
    )
    processes = [
        _FakeShardProcess([0], stdout=b"alpha passed\n"),
        _FakeShardProcess([0], stdout=b"bravo passed\n"),
    ]
    spawner = _StubSpawner(processes)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_discoverer=lambda _cwd: (
            "tests/test_bravo.py",
            "tests/test_alpha.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    assert [command[3 : command.index("-q")] for command, _cwd, _env in spawner.calls] == [
        ("tests/test_alpha.py",),
        ("tests/test_bravo.py",),
    ]
    assert all("-n" not in call[0] and "--dist" not in call[0] for call in spawner.calls)
    assert all(process.reaped for process in processes)
    env = spawner.calls[0][2]
    assert env[test_suites_module.TEST_TIMEOUT_ENV] == str(
        test_suites_module.DEFAULT_TEST_TIMEOUT_SECONDS
    )
    assert env["RALPH_PYTEST_SUITE_TIMEOUT_SECONDS"] == str(
        test_suites_module.DEFAULT_SUITE_TIMEOUT_SECONDS
    )
    assert "RALPH_VERIFY_REQUIRED_AUTO_INTEGRATE_E2E" not in env


def test_run_test_suites_preserves_only_its_own_project_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    spawner = _StubSpawner([_FakeShardProcess([0])])

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            spawner=spawner,
            file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
        )
        == 0
    )
    assert spawner.calls[0][2]["PYTHONPATH"] == str(tmp_path)


def test_run_test_suites_does_not_leak_another_environment_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setenv("PYTHONPATH", "/other/environment/site-packages")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    spawner = _StubSpawner([_FakeShardProcess([0])])

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            spawner=spawner,
            file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
        )
        == 0
    )
    assert "PYTHONPATH" not in spawner.calls[0][2]


def test_pytest_tmpdir_regression_shards_use_isolated_repo_basetemps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each shard bypasses pytest's shared numbered temporary root."""
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py", "tests/test_bravo.py"),
    )
    spawner = _StubSpawner([_FakeShardProcess([0]), _FakeShardProcess([0])])

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_discoverer=lambda _cwd: ("tests/test_alpha.py", "tests/test_bravo.py"),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0


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
    spawner = _StubSpawner([_FakeShardProcess([0]), _FakeShardProcess([0])])

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

    basetemps = tuple(
        Path(command[command.index("--basetemp") + 1]) for command, _cwd, _env in spawner.calls
    )
    assert exit_code == 0
    assert len(set(basetemps)) == 2
    assert {path.name for path in basetemps} == {"shard-0", "shard-1"}
    expected_parent = Path(tempfile.gettempdir()) / "ralph-pytest-shards"
    assert all(path.is_relative_to(expected_parent) for path in basetemps)
    assert not any(path.parent.exists() for path in basetemps)


def test_focused_auto_integrate_profile_shards_exact_registry_without_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    processes = [_FakeShardProcess([0]), _FakeShardProcess([0])]
    spawner = _StubSpawner(processes)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
        auto_integrate_e2e_only=True,
    )

    assigned_files = tuple(
        path for command, _cwd, _env in spawner.calls for path in command[3 : command.index("-q")]
    )
    assert exit_code == 0
    assert sorted(assigned_files) == sorted(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    assert len(assigned_files) == len(set(assigned_files))


def test_subprocess_e2e_profile_uses_canonical_marker_with_explicit_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    spawner = _StubSpawner([_FakeShardProcess([0])])
    monkeypatch.setattr(
        test_suites_module,
        "discover_subprocess_e2e_files",
        lambda _cwd: ("tests/test_e2e.py",),
    )

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            spawner=spawner,
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
            subprocess_e2e_only=True,
        )
        == 0
    )
    command = spawner.calls[0][0]
    assert command[3 : command.index("-q")] == ("tests/test_e2e.py",)
    marker_flag = command.index("-m", command.index("pytest") + 1)
    assert command[marker_flag + 1] == test_suites_module._SUBPROCESS_E2E_MARK_EXPRESSION
