"""Tests for the maintained test-suite runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph import test_suites as test_suites_module
from ralph.executor.process import ProcessResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    import pytest


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
        "not subprocess_e2e",
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
        "not subprocess_e2e",
    )
    runner = StubRunner({command: _result(command, returncode=2)})

    exit_code = test_suites_module.run_test_suites(cwd=tmp_path, runner=runner)

    assert exit_code == 2
    assert [call[0] for call in runner.calls] == [command]
