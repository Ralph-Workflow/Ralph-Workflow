"""Tests for the maintained test-suite runner."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import test_suites as test_suites_module

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES = (
    "tests/test_auto_integrate_conflict_e2e.py",
    "tests/test_auto_integrate_clone_conflict_e2e.py",
    "tests/test_auto_integrate_catchup_e2e.py",
    "tests/test_auto_integrate_worktree_prefix_e2e.py",
    "tests/test_auto_integrate_fail_closed_e2e.py",
    "tests/test_auto_integrate_end_to_end.py",
    "tests/test_auto_integrate_refresh_contract.py",
    "tests/test_auto_integrate_seams_e2e.py",
    "tests/test_auto_integrate_conflict_seams_e2e.py",
    "tests/test_auto_integrate_rebase_conflict_e2e.py",
    "tests/test_auto_integrate_real_agent_resolution_e2e.py",
    "tests/test_auto_integrate_fleet_conflict_e2e.py",
    "tests/test_auto_integrate_local_fleet_target_e2e.py",
    "tests/test_auto_integrate_remote_push.py",
    "tests/test_auto_integrate_remote_refresh.py",
    "tests/test_auto_integrate_stateless_seam.py",
    "tests/test_auto_integrate_env_pinning.py",
    "tests/test_auto_integrate_markerless_conflicts.py",
    "tests/test_auto_integrate_non_main_target.py",
    "tests/test_auto_integrate_rung4_self_resume.py",
    "tests/test_auto_integrate_recovery.py",
    "tests/test_auto_integrate_race.py",
    "tests/test_auto_integrate_worktree_sync.py",
    "tests/test_auto_integrate_catalog_e2e.py",
)


class FakeShardProcess:
    def __init__(
        self,
        returncodes: list[int | None],
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self._returncodes = list(returncodes)
        self._last_returncode: int | None = None
        self._stdout = stdout
        self._stderr = stderr
        self.terminated = False
        self.reaped = False

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
        self.reaped = True
        return self._stdout, self._stderr

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True
        self._last_returncode = -15


class StubSpawner:
    def __init__(self, processes: list[FakeShardProcess]) -> None:
        self._processes = list(processes)
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> FakeShardProcess:
        self.calls.append((tuple(command), cwd, dict(env)))
        return self._processes.pop(0)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_partition_selected_files_assigns_every_file_once_deterministically() -> None:
    selected = (
        "tests/test_delta.py",
        "tests/test_alpha.py",
        "tests/test_charlie.py",
        "tests/test_bravo.py",
    )

    shards = test_suites_module.partition_selected_files(selected, worker_count=3)

    assert shards == (
        ("tests/test_alpha.py", "tests/test_delta.py"),
        ("tests/test_bravo.py",),
        ("tests/test_charlie.py",),
    )
    test_suites_module.validate_exact_file_assignment(selected, shards)


def test_partition_selected_files_balances_static_weights_deterministically() -> None:
    selected = (
        "tests/test_alpha.py",
        "tests/test_bravo.py",
        "tests/test_charlie.py",
        "tests/test_delta.py",
    )
    weights = {
        "tests/test_alpha.py": 8,
        "tests/test_bravo.py": 7,
        "tests/test_charlie.py": 6,
        "tests/test_delta.py": 5,
    }

    shards = test_suites_module.partition_selected_files(
        selected,
        worker_count=2,
        file_weights=weights,
    )

    assert shards == (
        ("tests/test_alpha.py", "tests/test_delta.py"),
        ("tests/test_bravo.py", "tests/test_charlie.py"),
    )
    test_suites_module.validate_exact_file_assignment(selected, shards)


def test_estimate_test_file_weight_counts_sync_async_and_method_tests() -> None:
    source = """
def test_top_level() -> None:
    pass

async def test_async_case() -> None:
    pass

class TestCases:
    def test_method(self) -> None:
        pass

# def test_commented_out() -> None:
"""

    assert test_suites_module.estimate_test_file_weight(source) == 3
    assert test_suites_module.estimate_test_file_weight("# helper only\n") == 1


def test_validate_exact_file_assignment_rejects_duplicate_file() -> None:
    selected = ("tests/test_alpha.py", "tests/test_bravo.py")
    shards = (("tests/test_alpha.py",), ("tests/test_alpha.py", "tests/test_bravo.py"))

    with pytest.raises(RuntimeError, match=r"duplicate.*tests/test_alpha.py"):
        test_suites_module.validate_exact_file_assignment(selected, shards)


def test_validate_exact_file_assignment_rejects_missing_and_unexpected_files() -> None:
    selected = ("tests/test_alpha.py", "tests/test_bravo.py")
    shards = (("tests/test_alpha.py", "tests/test_charlie.py"),)

    with pytest.raises(
        RuntimeError,
        match=r"missing.*tests/test_bravo.py.*unexpected.*tests/test_charlie.py",
    ):
        test_suites_module.validate_exact_file_assignment(selected, shards)


def test_partition_selected_files_rejects_non_positive_worker_count() -> None:
    with pytest.raises(ValueError, match="worker_count must be positive"):
        test_suites_module.partition_selected_files(("tests/test_alpha.py",), worker_count=0)


def test_static_discovery_finds_pytest_patterns_and_required_files() -> None:
    discovered = test_suites_module.discover_test_files(Path.cwd())

    assert discovered == tuple(sorted(set(discovered)))
    assert all(
        Path(path).name.startswith("test_") or Path(path).name.endswith("_test.py")
        for path in discovered
    )
    assert set(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES) <= set(discovered)


def test_run_test_suites_runs_disjoint_plain_pytest_shards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py", "tests/test_bravo.py"),
    )
    processes = [
        FakeShardProcess([0], stdout=b"alpha passed\n"),
        FakeShardProcess([0], stdout=b"bravo passed\n"),
    ]
    spawner = StubSpawner(processes)

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
    assert [call[0] for call in spawner.calls] == [
        test_suites_module._shard_command(("tests/test_alpha.py",)),
        test_suites_module._shard_command(("tests/test_bravo.py",)),
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
    processes = [FakeShardProcess([0]), FakeShardProcess([0])]
    spawner = StubSpawner(processes)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
        auto_integrate_e2e_only=True,
    )

    assigned_files = tuple(
        path
        for command, _cwd, _env in spawner.calls
        for path in command[3 : command.index("-q")]
    )
    assert exit_code == 0
    assert sorted(assigned_files) == sorted(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    assert len(assigned_files) == len(set(assigned_files))


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
    failed = FakeShardProcess([1], stderr=b"failed\n")
    sibling = FakeShardProcess([None])
    spawner = StubSpawner([failed, sibling])

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
    processes = [FakeShardProcess([None]), FakeShardProcess([None])]
    spawner = StubSpawner(processes)
    clock = FakeClock()

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


def test_run_test_suites_charges_static_discovery_to_parent_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    clock = FakeClock()
    spawner = StubSpawner([])

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
