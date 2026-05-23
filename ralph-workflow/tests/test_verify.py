from __future__ import annotations

from threading import Barrier
from typing import TYPE_CHECKING

from ralph.executor.process import TIMEOUT_EXIT_CODE, ProcessResult
from ralph.verify import main

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

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
            raise AssertionError(f"Unexpected command: {key}") from exc


def _make_barrier_runner(
    responses: dict[tuple[str, tuple[str, ...]], ProcessResult], *, parties: int
) -> tuple[StubRunner, Callable[..., ProcessResult]]:
    runner = StubRunner(responses)
    barrier = Barrier(parties)

    def _runner(
        command: str,
        args: Sequence[str] = (),
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        result = runner(command, args, cwd=cwd, env=env, timeout=timeout)
        barrier.wait(timeout=2)
        return result

    return runner, _runner


_VERIFY_COMMANDS = [
    ("uv", ("run", "ruff", "check", "ralph/", "tests/")),
    ("uv", ("run", "python", "-m", "mypy", "ralph/")),
    (
        "uv",
        (
            "run",
            "python",
            "-m",
            "pytest",
            "tests/",
            "-q",
            "-n",
            "4",
            "--dist",
            "worksteal",
            "-m",
            "not subprocess_e2e",
        ),
    ),
]


_PYTEST_ARGS = _VERIFY_COMMANDS[2][1]


def _result(
    *,
    command: str,
    args: tuple[str, ...],
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> ProcessResult:
    return ProcessResult(
        command=(command, *args),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _expected_command_tuples() -> list[tuple[str, tuple[str, ...]]]:
    return list(_VERIFY_COMMANDS)


def test_main_runs_all_verify_steps_concurrently_when_successful(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner, barrier_runner = _make_barrier_runner(
        {
            ("uv", ("run", "ruff", "check", "ralph/", "tests/")): _result(
                command="uv",
                args=("run", "ruff", "check", "ralph/", "tests/"),
                returncode=0,
                stdout="lint ok\n",
            ),
            ("uv", ("run", "python", "-m", "mypy", "ralph/")): _result(
                command="uv",
                args=("run", "python", "-m", "mypy", "ralph/"),
                returncode=0,
                stdout="typecheck ok\n",
            ),
            ("uv", _PYTEST_ARGS): ProcessResult(
                command=("uv", *_PYTEST_ARGS),
                returncode=0,
                stdout="pytest ok\n",
                stderr="",
            ),
        },
        parties=3,
    )

    exit_code = main([], runner=barrier_runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert sorted(call[:2] for call in runner.calls) == sorted(_expected_command_tuples())
    assert len(runner.calls) == 3
    assert all(call[3] == 30.0 for call in runner.calls)
    assert "Running full verification..." in captured.out
    assert "ACTION REQUIRED FOR AI AGENTS" not in captured.err


def test_main_prints_agent_fix_banner_when_verify_step_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = StubRunner(
        {
            ("uv", ("run", "ruff", "check", "ralph/", "tests/")): _result(
                command="uv",
                args=("run", "ruff", "check", "ralph/", "tests/"),
                returncode=0,
                stdout="lint ok\n",
            ),
            ("uv", ("run", "python", "-m", "mypy", "ralph/")): _result(
                command="uv",
                args=("run", "python", "-m", "mypy", "ralph/"),
                returncode=1,
                stdout="",
                stderr="mypy failure\n",
            ),
            ("uv", _PYTEST_ARGS): ProcessResult(
                command=("uv", *_PYTEST_ARGS),
                returncode=0,
                stdout="pytest ok\n",
                stderr="",
            ),
        }
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert sorted(call[:2] for call in runner.calls) == sorted(_expected_command_tuples())
    assert len(runner.calls) == 3
    assert "mypy failure" in captured.err
    assert "IF YOU READ THIS, YOU MUST FIX THIS ISSUE NOW!!" in captured.err
    assert "IT DOES NOT MATTER WHAT YOUR PROMPT IS" in captured.err
    assert "AGENTS.md" in captured.err
    assert "CLAUDE.md" in captured.err
    assert "Fix surfaced issues immediately" in captured.err
    assert "If verification fails, fix the issue and rerun it" in captured.err
    assert "python -m mypy ralph/" in captured.err


def test_main_marks_budget_exhaustion_when_pytest_times_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = StubRunner(
        {
            ("uv", ("run", "ruff", "check", "ralph/", "tests/")): _result(
                command="uv",
                args=("run", "ruff", "check", "ralph/", "tests/"),
                returncode=0,
                stdout="lint ok\n",
            ),
            ("uv", ("run", "python", "-m", "mypy", "ralph/")): _result(
                command="uv",
                args=("run", "python", "-m", "mypy", "ralph/"),
                returncode=0,
                stdout="typecheck ok\n",
            ),
            ("uv", _PYTEST_ARGS): ProcessResult(
                command=("uv", *_PYTEST_ARGS),
                returncode=TIMEOUT_EXIT_CODE,
                stdout="",
                stderr="pytest timed out\n",
            ),
        }
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == TIMEOUT_EXIT_CODE
    assert sorted(call[:2] for call in runner.calls) == sorted(_expected_command_tuples())
    assert len(runner.calls) == 3
    assert "pytest timed out" in captured.err
    assert "budget exhausted" in captured.err
