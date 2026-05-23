from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.executor.process import ProcessResult
from ralph.verify import main

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest


class StubRunner:
    def __init__(self, responses: dict[tuple[str, tuple[str, ...]], ProcessResult]) -> None:
        self._responses = dict(responses)
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
        key = (command, tuple(args))
        self.calls.append((command, tuple(args), cwd, timeout))
        try:
            return self._responses[key]
        except KeyError as exc:
            if command == "python" and tuple(args[:2]) == ("-m", "pytest"):
                return ProcessResult(
                    command=("python", *args),
                    returncode=0,
                    stdout="",
                    stderr="",
                )
            raise AssertionError(f"Unexpected command: {key}") from exc


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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _file_weight(path: Path) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return 1
    weight = sum(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )
    return weight or 1


def _expected_test_files() -> set[str]:
    root = _repo_root()
    return {
        str(path.relative_to(root))
        for path in (root / "tests").rglob("test*.py")
        if path.name != "conftest.py"
    }


def _shard_count() -> int:
    root = _repo_root()
    files = sorted(
        path for path in (root / "tests").rglob("test*.py") if path.name != "conftest.py"
    )
    return min(16, len(files))


def _build_expected_commands() -> tuple[tuple[str, ...], ...]:
    root = _repo_root()
    files = sorted(
        path for path in (root / "tests").rglob("test*.py") if path.name != "conftest.py"
    )
    weighted_files = sorted(
        ((path, _file_weight(path)) for path in files),
        key=lambda item: (-item[1], str(item[0])),
    )
    shard_count = min(16, len(weighted_files))
    shard_files: list[list[str]] = [[] for _ in range(shard_count)]
    shard_weights = [0 for _ in range(shard_count)]
    for path, weight in weighted_files:
        shard_index = min(
            range(shard_count),
            key=lambda index: (shard_weights[index], len(shard_files[index]), index),
        )
        shard_files[shard_index].append(str(path.relative_to(root)))
        shard_weights[shard_index] += weight
    return tuple(
        (
            "-m",
            "pytest",
            *files,
            "-q",
            "-m",
            "not subprocess_e2e",
        )
        for files in shard_files
        if files
    )


def _pytest_file_args(
    call: tuple[str, tuple[str, ...], str | Path | None, float | None]
) -> tuple[str, ...]:
    args = call[1]
    assert args[:2] == ("-m", "pytest")
    q_index = args.index("-q")
    assert args[q_index : q_index + 3] == ("-q", "-m", "not subprocess_e2e")
    return args[2:q_index]


def test_main_runs_all_verify_steps_when_successful(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shard_count = _shard_count()
    runner = StubRunner(
        {
            ("make", ("lint",)): _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            ("make", ("typecheck",)): _result(
                args=("typecheck",), returncode=0, stdout="typecheck ok\n"
            ),
        }
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert runner.calls[:2] == [
        ("make", ("lint",), tmp_path, None),
        ("make", ("typecheck",), tmp_path, None),
    ]
    pytest_calls = [call for call in runner.calls if call[0] == "python"]
    assert len(pytest_calls) == shard_count
    assert {
        path for call in pytest_calls for path in _pytest_file_args(call)
    } == _expected_test_files()
    assert all(_pytest_file_args(call) for call in pytest_calls)
    assert "Running full verification..." in captured.out
    assert "ACTION REQUIRED FOR AI AGENTS" not in captured.err


def test_main_prints_agent_fix_banner_when_verify_step_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = StubRunner(
        {
            ("make", ("lint",)): _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            ("make", ("typecheck",)): _result(
                args=("typecheck",), returncode=1, stdout="", stderr="mypy failure\n"
            ),
        }
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
    shard_count = _shard_count()
    runner = StubRunner(
        {
            ("make", ("lint",)): _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            ("make", ("typecheck",)): _result(
                args=("typecheck",), returncode=0, stdout="typecheck ok\n"
            ),
        }
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    assert exit_code == 0
    pytest_calls = [call for call in runner.calls if call[0] == "python"]
    assert len(pytest_calls) == shard_count
    for call in pytest_calls:
        assert call[3] is not None
        assert 0 < call[3] <= 30.0
        assert _pytest_file_args(call)
    assert {
        path for call in pytest_calls for path in _pytest_file_args(call)
    } == _expected_test_files()


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
            if command == "python" and args[:2] == ("-m", "pytest"):
                raise AssertionError(
                    "pytest should not be called when budget is exhausted"
                )
            return super().__call__(command, args, cwd=cwd, env=env, timeout=timeout)

    runner = BudgetExhaustedRunner(
        {
            ("make", ("lint",)): _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            ("make", ("typecheck",)): _result(
                args=("typecheck",), returncode=0, stdout="typecheck ok\n"
            ),
        }
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
        call[0] == "python" and call[1][:2] == ("-m", "pytest")
        for call in runner.calls
    )

    captured = capsys.readouterr()
    assert "budget exhausted" in captured.err
    assert "ACTION REQUIRED FOR AI AGENTS" in captured.err
