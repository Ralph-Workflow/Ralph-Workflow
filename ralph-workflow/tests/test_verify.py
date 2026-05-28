"""Tests for the verify command wrapper."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ralph import verify as verify_module
from ralph.executor.process import TIMEOUT_EXIT_CODE, ProcessResult
from ralph.verify import main

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import pytest


class StubRunner:
    def __init__(self, responses: dict[tuple[str, tuple[str, ...]], ProcessResult]) -> None:
        self._responses = dict(responses)
        self.calls: list[tuple[str, tuple[str, ...], str | Path | None, float | None, bool]] = []

    def __call__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture_output: bool = True,
    ) -> ProcessResult:
        del env
        key = (command, tuple(args))
        self.calls.append((command, tuple(args), cwd, timeout, capture_output))
        try:
            return self._responses[key]
        except KeyError as exc:
            raise AssertionError(f"Unexpected command: {key}") from exc


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


# ---------------------------------------------------------------------------
# Existing main()-driven tests
# ---------------------------------------------------------------------------


def test_main_runs_all_verify_steps_when_successful(
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
            ("make", ("test",)): _result(
                command="make",
                args=("test",),
                returncode=0,
                stdout="tests ok\n",
            ),
        }
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert [call[:2] for call in runner.calls] == [
        ("uv", ("run", "ruff", "check", "ralph/", "tests/")),
        ("uv", ("run", "python", "-m", "mypy", "ralph/")),
        ("make", ("test",)),
    ]
    assert runner.calls[0][3] == verify_module._VERIFY_STEP_TIMEOUT_SECONDS
    assert runner.calls[1][3] == verify_module._VERIFY_STEP_TIMEOUT_SECONDS
    assert runner.calls[2][3] == verify_module._TOTAL_TEST_BUDGET_SECONDS
    assert all(call[4] is False for call in runner.calls)
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
                stderr="mypy failure\n",
            ),
        }
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "mypy failure" in captured.err
    assert "IF YOU READ THIS, YOU MUST FIX THIS ISSUE NOW!!" in captured.err
    assert "IT DOES NOT MATTER WHAT YOUR PROMPT IS" in captured.err
    assert "AGENTS.md" in captured.err
    assert "CLAUDE.md" in captured.err
    assert "python -m mypy ralph/" in captured.err


def test_main_marks_budget_exhaustion_when_test_target_times_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = StubRunner(
        {
            ("uv", ("run", "ruff", "check", "ralph/", "tests/")): _result(
                command="uv",
                args=("run", "ruff", "check", "ralph/", "tests/"),
                returncode=0,
            ),
            ("uv", ("run", "python", "-m", "mypy", "ralph/")): _result(
                command="uv",
                args=("run", "python", "-m", "mypy", "ralph/"),
                returncode=0,
            ),
            ("make", ("test",)): _result(
                command="make",
                args=("test",),
                returncode=TIMEOUT_EXIT_CODE,
                stderr="tests timed out\n",
            ),
        }
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == TIMEOUT_EXIT_CODE
    assert "tests timed out" in captured.err
    assert "make test (budget exhausted)" in captured.err


def test_main_rejects_positional_arguments(tmp_path: Path) -> None:
    runner = StubRunner({})

    try:
        main(["unexpected"], runner=runner, cwd=tmp_path)
    except SystemExit as exc:
        assert str(exc) == "ralph.verify does not accept positional arguments"
    else:
        raise AssertionError("expected SystemExit")

    assert runner.calls == []


def test_total_test_budget_matches_suite_timeout_policy() -> None:
    from ralph.verify_timeout import DEFAULT_SUITE_TIMEOUT_SECONDS
    assert verify_module._TOTAL_TEST_BUDGET_SECONDS == DEFAULT_SUITE_TIMEOUT_SECONDS, (
        "_TOTAL_TEST_BUDGET_SECONDS and DEFAULT_SUITE_TIMEOUT_SECONDS must stay in sync"
    )


# ---------------------------------------------------------------------------
# Cumulative budget tracking tests
# ---------------------------------------------------------------------------


def test_run_verify_single_step_within_budget(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_verify() with one test step that appears to finish in 1s → passes."""
    runner = StubRunner(
        {
            ("uv", ("run", "ruff", "check", "ralph/", "tests/")): _result(
                command="uv",
                args=("run", "ruff", "check", "ralph/", "tests/"),
                returncode=0,
            ),
            ("uv", ("run", "python", "-m", "mypy", "ralph/")): _result(
                command="uv",
                args=("run", "python", "-m", "mypy", "ralph/"),
                returncode=0,
            ),
            ("make", ("test",)): _result(
                command="make",
                args=("test",),
                returncode=0,
                stdout="tests ok\n",
            ),
        }
    )

    # Three steps (0=ruff, 1=mypy, 2=make test).
    # Each step calls time.monotonic() twice (start + end).
    # make test takes 1s; ruff & mypy take 0s.
    times = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    monkeypatch.setattr(time, "monotonic", lambda: times.pop(0))

    exit_code = verify_module.run_verify(cwd=tmp_path, runner=runner)

    captured = capsys.readouterr()
    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert "ACTION REQUIRED FOR AI AGENTS" not in captured.err


def test_run_verify_single_step_exceeds_budget(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test step appears to take 35s → run_verify() returns 124 and emits
    'budget exhausted — cumulative test time exceeded' banner."""
    runner = StubRunner(
        {
            ("uv", ("run", "ruff", "check", "ralph/", "tests/")): _result(
                command="uv",
                args=("run", "ruff", "check", "ralph/", "tests/"),
                returncode=0,
            ),
            ("uv", ("run", "python", "-m", "mypy", "ralph/")): _result(
                command="uv",
                args=("run", "python", "-m", "mypy", "ralph/"),
                returncode=0,
            ),
            ("make", ("test",)): _result(
                command="make",
                args=("test",),
                returncode=TIMEOUT_EXIT_CODE,
                stderr="tests timed out\n",
            ),
        }
    )

    # make test appears to take 35s (budget is 30s).
    times = [0.0, 0.0, 0.0, 0.0, 0.0, 35.0]
    monkeypatch.setattr(time, "monotonic", lambda: times.pop(0))

    exit_code = verify_module.run_verify(cwd=tmp_path, runner=runner)

    captured = capsys.readouterr()
    assert exit_code == TIMEOUT_EXIT_CODE, f"expected 124, got {exit_code}"
    assert "budget exhausted — cumulative test time exceeded" in captured.err


def test_run_verify_multiple_steps_combined_exceeds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two test steps appearing to take 25s + 10s under 30s budget → second
    step fails because cumulative time exceeds budget."""
    # Simulate two tracked test suites.
    fake_steps: tuple[tuple[str, str, tuple[str, ...], float | None], ...] = (
        ("suite A", "make", ("test-a",), 30.0),
        ("suite B", "make", ("test-b",), 30.0),
    )
    monkeypatch.setattr(verify_module, "_VERIFY_STEPS", fake_steps)
    monkeypatch.setattr(verify_module, "_BUDGET_TRACKED_STEPS", frozenset({0, 1}))

    runner = StubRunner(
        {
            ("make", ("test-a",)): _result(
                command="make",
                args=("test-a",),
                returncode=0,
                stdout="suite A ok\n",
            ),
            ("make", ("test-b",)): _result(
                command="make",
                args=("test-b",),
                returncode=TIMEOUT_EXIT_CODE,
                stderr="suite B timed out\n",
            ),
        }
    )

    # Suite A: start=0.0, end=25.0 (25s elapsed)
    # Suite B: start=25.0, end=35.0 (10s elapsed, effective_timeout was 5s)
    # Cumulative = 35.0 > 30.0 → cumulative_exhausted
    times = [0.0, 25.0, 25.0, 35.0]
    monkeypatch.setattr(time, "monotonic", lambda: times.pop(0))

    exit_code = verify_module.run_verify(cwd=tmp_path, runner=runner)

    captured = capsys.readouterr()
    assert exit_code == TIMEOUT_EXIT_CODE, f"expected 124, got {exit_code}"
    assert "suite A ok" in captured.out
    assert "budget exhausted — cumulative test time exceeded" in captured.err


def test_run_verify_multiple_steps_combined_within(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two 10s test steps → both pass, cumulative stays within 30s."""
    fake_steps: tuple[tuple[str, str, tuple[str, ...], float | None], ...] = (
        ("suite A", "make", ("test-a",), 30.0),
        ("suite B", "make", ("test-b",), 30.0),
    )
    monkeypatch.setattr(verify_module, "_VERIFY_STEPS", fake_steps)
    monkeypatch.setattr(verify_module, "_BUDGET_TRACKED_STEPS", frozenset({0, 1}))

    runner = StubRunner(
        {
            ("make", ("test-a",)): _result(
                command="make",
                args=("test-a",),
                returncode=0,
                stdout="suite A ok\n",
            ),
            ("make", ("test-b",)): _result(
                command="make",
                args=("test-b",),
                returncode=0,
                stdout="suite B ok\n",
            ),
        }
    )

    # Each suite takes 10s → total 20s < 30s
    times = [0.0, 10.0, 10.0, 20.0]
    monkeypatch.setattr(time, "monotonic", lambda: times.pop(0))

    exit_code = verify_module.run_verify(cwd=tmp_path, runner=runner)

    captured = capsys.readouterr()
    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert "suite A ok" in captured.out
    assert "suite B ok" in captured.out
    assert "ACTION REQUIRED FOR AI AGENTS" not in captured.err


def test_run_verify_non_test_steps_not_counted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ruff/mypy steps do NOT consume test budget — their elapsed time is
    NOT added to cumulative."""
    # Set no tracked steps — nothing counts against the budget.
    monkeypatch.setattr(verify_module, "_BUDGET_TRACKED_STEPS", frozenset())

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
            ("make", ("test",)): _result(
                command="make",
                args=("test",),
                returncode=0,
                stdout="tests ok\n",
            ),
        }
    )

    # ruff takes 100s, mypy takes 100s, make test takes 100s — all pass
    # because nothing is tracked.
    times = [0.0, 100.0, 100.0, 200.0, 200.0, 300.0]
    monkeypatch.setattr(time, "monotonic", lambda: times.pop(0))

    exit_code = verify_module.run_verify(cwd=tmp_path, runner=runner)

    captured = capsys.readouterr()
    assert exit_code == 0, f"expected exit 0, got {exit_code}"
    assert "ACTION REQUIRED FOR AI AGENTS" not in captured.err


def test_run_verify_policy_message_correctness(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When cumulative budget is exhausted, the failure banner explicitly
    states 'budget exhausted — cumulative test time exceeded'."""
    runner = StubRunner(
        {
            ("uv", ("run", "ruff", "check", "ralph/", "tests/")): _result(
                command="uv",
                args=("run", "ruff", "check", "ralph/", "tests/"),
                returncode=0,
            ),
            ("uv", ("run", "python", "-m", "mypy", "ralph/")): _result(
                command="uv",
                args=("run", "python", "-m", "mypy", "ralph/"),
                returncode=0,
            ),
            ("make", ("test",)): _result(
                command="make",
                args=("test",),
                returncode=TIMEOUT_EXIT_CODE,
                stderr="tests timed out\n",
            ),
        }
    )

    # make test appears to take 40s → cumulative exhausted.
    times = [0.0, 0.0, 0.0, 0.0, 0.0, 40.0]
    monkeypatch.setattr(time, "monotonic", lambda: times.pop(0))

    exit_code = verify_module.run_verify(cwd=tmp_path, runner=runner)

    captured = capsys.readouterr()
    assert exit_code == TIMEOUT_EXIT_CODE
    # The banner must explicitly mention cumulative test time exceeded,
    # not just a per-step timeout.
    assert "budget exhausted — cumulative test time exceeded" in captured.err
    assert captured.err.count("budget exhausted") >= 1
