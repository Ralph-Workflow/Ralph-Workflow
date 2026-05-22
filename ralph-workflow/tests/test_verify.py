from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.executor.process import ProcessResult
from ralph.verify import main

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import pytest


class StubRunner:
    def __init__(self, results: list[ProcessResult]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, tuple[str, ...], str | Path | None, float | None]] = []

    def __call__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        del env
        self.calls.append((command, tuple(args), cwd, timeout))
        return self._results.pop(0)


def _result(
    *,
    args: tuple[str, ...],
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> ProcessResult:
    return ProcessResult(
        command=("make", *args),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_main_runs_all_verify_steps_when_successful(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest_cmd = (
        "-m", "pytest", "tests", "-q", "-n", "4",
        "--dist", "worksteal", "-m", "not subprocess_e2e",
    )
    runner = StubRunner(
        [
            _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            _result(args=("typecheck",), returncode=0, stdout="typecheck ok\n"),
            ProcessResult(
                command=("python", *pytest_cmd),
                returncode=0,
                stdout="test ok\n",
                stderr="",
            ),
        ]
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert [call[:2] for call in runner.calls] == [
        ("make", ("lint",)),
        ("make", ("typecheck",)),
        ("python", pytest_cmd),
    ]
    assert "Running full verification..." in captured.out
    assert "ACTION REQUIRED FOR AI AGENTS" not in captured.err


def test_main_prints_agent_fix_banner_when_verify_step_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = StubRunner(
        [
            _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            _result(
                args=("typecheck",),
                returncode=1,
                stdout="",
                stderr="mypy failure\n",
            ),
        ]
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert [call[:2] for call in runner.calls] == [
        ("make", ("lint",)),
        ("make", ("typecheck",)),
    ]
    assert "mypy failure" in captured.err
    assert "IF YOU READ THIS, YOU MUST FIX THIS ISSUE NOW!!" in captured.err
    assert "IT DOES NOT MATTER WHAT YOUR PROMPT IS" in captured.err
    assert "AGENTS.md" in captured.err
    assert "CLAUDE.md" in captured.err
    assert "Fix surfaced issues immediately" in captured.err
    assert "If verification fails, fix the issue and rerun it" in captured.err
    assert "make typecheck" in captured.err


def test_main_passes_remaining_budget_to_pytest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify pytest receives only the remaining budget after lint/typecheck."""
    pytest_cmd = (
        "-m", "pytest", "tests", "-q", "-n", "4",
        "--dist", "worksteal", "-m", "not subprocess_e2e",
    )
    runner = StubRunner(
        [
            _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            _result(args=("typecheck",), returncode=0, stdout="typecheck ok\n"),
            ProcessResult(
                command=("python", *pytest_cmd),
                returncode=0,
                stdout="test ok\n",
                stderr="",
            ),
        ]
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    assert exit_code == 0
    # Last call should be pytest with a positive timeout
    last_call = runner.calls[-1]
    assert last_call[0] == "python"
    assert last_call[1] == pytest_cmd
    # Timeout should be positive but less than or equal to 30 (the full budget)
    assert last_call[3] is not None
    assert 0 < last_call[3] <= 30.0


def test_main_refuses_to_run_pytest_when_budget_exhausted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify that when pre-pytest steps exhaust the budget, pytest is not called."""
    # Simulate a StubRunner where lint and typecheck each "consume" time
    # by returning after a long enough elapsed time.
    # We cannot actually elapsed time in the stub, but we can check the
    # timeout-handling logic by observing that pytest is never called when
    # the budget is reported as exhausted.
    #
    # Since StubRunner doesn't measure real time, we test the branch directly
    # by having the runner raise an error on pytest if it's incorrectly called.
    # Instead, we verify the exit path by checking that pytest is not in calls.
    class BudgetExhaustedRunner(StubRunner):
        def __call__(
            self,
            command: str,
            args: Sequence[str] = (),
            *,
            cwd: str | Path | None = None,
            env: dict[str, str] | None = None,
            timeout: float | None = None,
        ) -> ProcessResult:
            # If pytest is called with timeout <= 0, return failure
            if command == "python" and args == ("-m", "pytest"):
                raise AssertionError(
                    "pytest should not be called when budget is exhausted"
                )
            return super().__call__(command, args, cwd=cwd, env=env, timeout=timeout)

    runner = BudgetExhaustedRunner(
        [
            _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            _result(args=("typecheck",), returncode=0, stdout="typecheck ok\n"),
        ]
    )

    # Patch time.monotonic to return a value that makes remaining_budget <= 0
    import time
    from unittest.mock import patch

    # Simulate: 31 seconds have elapsed (budget exhausted)
    # time.monotonic returns 1000, then 1031 (31 seconds later)
    # This makes remaining_budget = 30 - 31 = -1, triggering the exhausted path
    with patch.object(time, "monotonic", side_effect=[1000.0, 1031.0]):
        exit_code = main([], runner=runner, cwd=tmp_path)

    # Should fail without calling pytest
    assert exit_code == 1
    # No pytest call should have been made
    assert not any(
        call[0] == "python" and call[1] == ("-m", "pytest")
        for call in runner.calls
    )

    captured = capsys.readouterr()
    assert "budget exhausted" in captured.err
    assert "ACTION REQUIRED FOR AI AGENTS" in captured.err
