"""Orchestration, worker-count, and timeout tests for the test-suite runner.

Split out of :mod:`tests.test_test_suites` so the shared suite stays under
the maintained 1000-line-per-file cap. The shard fake classes live in
:mod:`tests._test_test_suites_helpers` to keep both this file and the
discovery suite free of repeated fakes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import test_suites as test_suites_module
from tests._test_test_suites_helpers import (
    _FakeClock,
    _FakeShardProcess,
    _StubSpawner,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES = (
    "tests/test_auto_integrate_end_to_end.py",
    "tests/test_tool_git_read_path_validation.py",
)


def test_pytest_shard_processes_disable_background_reaping_and_event_logging() -> None:
    """Shard teardown owns lifecycle cleanup without per-shard background work."""
    policy = test_suites_module._PYTEST_SHARD_PROCESS_MANAGER.policy

    assert policy.log_events is False
    assert policy.enable_zombie_reaper is False


@pytest.mark.parametrize(
    ("cpu_count", "expected_workers"),
    (
        (None, "1"),
        (1, "1"),
        (2, "1"),
        (16, "15"),
        (32, "31"),
        (64, "32"),
    ),
)
def test_auto_worker_count_preserves_one_core_and_caps_at_thirty_two(
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    expected_workers: str,
) -> None:
    """Auto profile caps the shard count at ``available_cores - 1``.

    The 32-core CI profile gets 31 shards (one core reserved for the
    runner process and the per-shard drain / SIGCHLD cleanup loop).
    The 64-core profile is capped at ``_MAX_PYTEST_WORKERS = 32``.
    Smaller hosts preserve one core for the runner. The default
    resolution was tightened from a 12-shard cap (which drove the
    slowest shard past 60s under host load) to ``available_cores - 1``
    so the slowest shard fits comfortably under the immutable 60s
    combined budget.
    """
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
        test_suites_module.timeout_seconds_from_env(
            test_suites_module.TEST_TIMEOUT_ENV,
            test_suites_module.DEFAULT_TEST_TIMEOUT_SECONDS,
        )
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


def test_required_auto_integrate_e2e_registry_matches_verification_contract() -> None:
    assert (
        test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES
        == EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    )
    assert len(set(test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES)) == len(
        EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    )


def test_required_auto_integrate_selection_fails_closed_when_file_is_missing() -> None:
    selected = EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES[:-1]

    try:
        test_suites_module.validate_required_auto_integrate_selection(selected)
    except RuntimeError as exc:
        assert EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES[-1] in str(exc)
    else:
        raise AssertionError("missing required auto-integrate file was accepted")


def test_required_auto_integrate_selection_accepts_complete_registry() -> None:
    test_suites_module.validate_required_auto_integrate_selection(
        EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    )


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
