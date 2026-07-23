"""Tests for the maintained test-suite runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph import test_suites as test_suites_module
from ralph.executor.process import ProcessResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    import pytest


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


class StubRunner:
    def __init__(self, responses: dict[tuple[str, ...], ProcessResult]) -> None:
        self._responses = dict(responses)
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str] | None, float]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        suite_timeout_seconds: float,
    ) -> ProcessResult:
        key = tuple(command)
        self.calls.append((key, cwd, dict(env) if env is not None else None, suite_timeout_seconds))
        try:
            return self._responses[key]
        except KeyError as exc:
            raise AssertionError(f"Unexpected command: {key}") from exc


def _result(command: tuple[str, ...], returncode: int = 0) -> ProcessResult:
    return ProcessResult(command=command, returncode=returncode, stdout="", stderr="")


def test_run_test_suites_runs_single_budgeted_verification_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "5")
    command = (
        test_suites_module.sys.executable,
        "-m",
        "pytest",
        "tests/",
        "-q",
        "-n",
        "5",
        "--dist",
        "worksteal",
        "-m",
        "(not subprocess_e2e and not smoke) or required_auto_integrate_e2e",
    )
    runner = StubRunner({command: _result(command)})

    exit_code = test_suites_module.run_test_suites(cwd=tmp_path, runner=runner)

    assert exit_code == 0
    assert [call[0] for call in runner.calls] == [command]
    assert all(call[1] == tmp_path for call in runner.calls)
    assert all(call[3] == test_suites_module.DEFAULT_SUITE_TIMEOUT_SECONDS for call in runner.calls)
    env = runner.calls[0][2]
    assert env is not None
    assert env[test_suites_module.TEST_TIMEOUT_ENV] == str(
        test_suites_module.DEFAULT_TEST_TIMEOUT_SECONDS
    )
    assert env["RALPH_PYTEST_SUITE_TIMEOUT_SECONDS"] == str(
        test_suites_module.DEFAULT_SUITE_TIMEOUT_SECONDS
    )


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


def test_focused_auto_integrate_command_uses_required_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "5")

    assert test_suites_module._auto_integrate_e2e_command() == (
        test_suites_module.sys.executable,
        "-m",
        "pytest",
        *EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES,
        "-q",
        "-n",
        "5",
        "--dist",
        "worksteal",
    )


def test_run_test_suites_returns_non_zero_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "5")
    command = (
        test_suites_module.sys.executable,
        "-m",
        "pytest",
        "tests/",
        "-q",
        "-n",
        "5",
        "--dist",
        "worksteal",
        "-m",
        "(not subprocess_e2e and not smoke) or required_auto_integrate_e2e",
    )
    runner = StubRunner({command: _result(command, returncode=2)})

    exit_code = test_suites_module.run_test_suites(cwd=tmp_path, runner=runner)

    assert exit_code == 2
    assert [call[0] for call in runner.calls] == [command]
