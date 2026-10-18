"""Subprocess-E2E PTY regression and process-pool termination tests for the test-suite runner.

Split out of :mod:`tests.test_test_suites` so the discovery + partitioning
suite stays under the maintained 1000-line-per-file cap. The shared shard
fake classes live in :mod:`tests._test_test_suites_helpers` to keep both
this file and the discovery / orchestration suites free of repeated fakes.

Scope: subprocess E2E PTY exclusivity (S-1) plus one-process termination /
reaping (S-5). The "no terminal lost" PTY completion contract has its own
file (see :mod:`tests.test_test_suites_pty_completion`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ralph.test_suites as test_suites_module
from tests._test_test_suites_helpers import (
    _FakeClock,
    _FakeShardProcess,
    _StubSpawner,
)

EXPECTED_EXCLUSIVE_SUBPROCESS_E2E_FILES = ("tests/agents/test_terminal_state_restored_on_exit.py",)


def test_subprocess_e2e_regression_exclusive_pty_registry_is_fixed_and_unique() -> None:
    """S-1: PTY restoration runs in exactly one explicit exclusive lane."""
    assert (
        test_suites_module.EXCLUSIVE_SUBPROCESS_E2E_FILES == EXPECTED_EXCLUSIVE_SUBPROCESS_E2E_FILES
    )
    assert len(set(test_suites_module.EXCLUSIVE_SUBPROCESS_E2E_FILES)) == len(
        EXPECTED_EXCLUSIVE_SUBPROCESS_E2E_FILES
    )


@pytest.mark.parametrize(
    "selected_files",
    (
        ("tests/test_general.py",),
        (
            "tests/test_general.py",
            "tests/agents/test_terminal_state_restored_on_exit.py",
            "tests/agents/test_terminal_state_restored_on_exit.py",
        ),
    ),
)
def test_subprocess_e2e_regression_exclusive_pty_selection_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected_files: tuple[str, ...],
) -> None:
    """S-1: missing or duplicate PTY selection must start no subprocesses."""
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    spawner = _StubSpawner([])
    monkeypatch.setattr(
        test_suites_module, "discover_subprocess_e2e_files", lambda _cwd: selected_files
    )

    with pytest.raises(RuntimeError, match="exclusive subprocess-E2E"):
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            spawner=spawner,
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
            subprocess_e2e_only=True,
        )

    assert spawner.calls == []


def test_subprocess_e2e_regression_runs_pty_after_general_shards_once_without_xdist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S-1: general shards complete before the one-process PTY lane starts."""
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setenv("PYTEST_XDIST_WORKERS_PER_SHARD", "2")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    spawner = _StubSpawner([])
    general_reap_order: list[int] = []
    general_one = _FakeShardProcess(
        [0],
        stdout=b"general one output\n",
        on_communicate=lambda: general_reap_order.append(len(spawner.calls)),
    )
    general_two = _FakeShardProcess(
        [0],
        stdout=b"general two output\n",
        on_communicate=lambda: general_reap_order.append(len(spawner.calls)),
    )
    exclusive = _FakeShardProcess([0], stdout=b"exclusive output\n")
    spawner._processes.extend((general_one, general_two, exclusive))
    monkeypatch.setattr(
        test_suites_module,
        "discover_subprocess_e2e_files",
        lambda _cwd: (
            "tests/test_bravo.py",
            "tests/agents/test_terminal_state_restored_on_exit.py",
            "tests/test_alpha.py",
        ),
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

    captured = capsys.readouterr()
    assert spawner.manifest_files == [
        ("tests/test_alpha.py",),
        ("tests/test_bravo.py",),
        EXPECTED_EXCLUSIVE_SUBPROCESS_E2E_FILES,
    ]
    assert general_reap_order == [2, 2]
    assert "-n" in spawner.calls[0][0]
    assert "-n" in spawner.calls[1][0]
    # The exclusive subprocess E2E shard uses 4 in-shard xdist workers
    # (see ``_EXCLUSIVE_SUBPROCESS_E2E_SHARD_XDIST_WORKERS``) so the
    # eight real-PTY regressions can run concurrently and fit inside
    # the 60s combined subprocess profile budget.
    assert "-n" in spawner.calls[2][0]
    exclusive_xdist_index = spawner.calls[2][0].index("-n")
    assert spawner.calls[2][0][exclusive_xdist_index + 1] == "4"
    assert captured.out.index("general one output") < captured.out.index("exclusive output")
    assert captured.out.index("general two output") < captured.out.index("exclusive output")
    assert all(
        process.reaped and process.orphans_cleaned
        for process in (general_one, general_two, exclusive)
    )


def test_durations_profile_constant_added_to_focused_profiles() -> None:
    assert "durations" in test_suites_module._FOCUSED_PROFILES


def test_terminate_and_reap_continues_when_one_process_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S-5: one shard's terminate raising must not skip the rest.

    A foreign exception from a real process manager (e.g. when its
    descendant accounting trips) cannot leak the rest of the shard
    pool. The runner logs a warning and continues termination
    + reaping of the remaining processes.
    """
    monkeypatch.setenv("PYTEST_WORKERS", "3")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    clock = _FakeClock()
    raising = _FakeShardProcess(
        [None], terminate_raises=OSError("simulated process manager failure")
    )
    sibling_a = _FakeShardProcess([None])
    sibling_b = _FakeShardProcess([None])
    processes = [raising, sibling_a, sibling_b]

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=5.0,
        spawner=_StubSpawner(processes),
        file_discoverer=lambda _cwd: (
            "tests/test_alpha.py",
            "tests/test_bravo.py",
            "tests/test_charlie.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    # All three processes must be reaped. The raising one is still
    # reaped via its ``communicate``; the other two were terminated
    # first then reaped. Skipping the rest of the pool would have
    # left two ``reaped == False`` (and the leak would have
    # inflated the budget drain on a busy host).
    assert all(process.reaped for process in processes)
    assert sibling_a.terminated
    assert sibling_b.terminated
    captured = capsys.readouterr()
    assert "shard terminate raised OSError" in captured.err
