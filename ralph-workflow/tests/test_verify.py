"""Tests for the verify command wrapper."""

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
    runner = StubRunner(
        [
            _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            _result(args=("typecheck",), returncode=0, stdout="typecheck ok\n"),
            _result(args=("test",), returncode=0, stdout="test ok\n"),
        ]
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert runner.calls == [
        ("make", ("lint",), tmp_path, None),
        ("make", ("typecheck",), tmp_path, None),
        ("make", ("test",), tmp_path, None),
    ]
    assert "Running full verification..." in captured.out
    assert "ACTION REQUIRED FOR AI AGENTS" not in captured.err


def test_main_stops_after_first_failing_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = StubRunner(
        [
            _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            _result(args=("typecheck",), returncode=1, stderr="mypy failure\n"),
        ]
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert runner.calls == [
        ("make", ("lint",), tmp_path, None),
        ("make", ("typecheck",), tmp_path, None),
    ]
    assert "mypy failure" in captured.err
    assert "IF YOU READ THIS, YOU MUST FIX THIS ISSUE NOW!!" in captured.err
    assert "make typecheck" in captured.err


def test_main_prints_banner_when_test_step_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = StubRunner(
        [
            _result(args=("lint",), returncode=0, stdout="lint ok\n"),
            _result(args=("typecheck",), returncode=0, stdout="typecheck ok\n"),
            _result(args=("test",), returncode=124, stderr="suite timeout\n"),
        ]
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 124
    assert runner.calls == [
        ("make", ("lint",), tmp_path, None),
        ("make", ("typecheck",), tmp_path, None),
        ("make", ("test",), tmp_path, None),
    ]
    assert "suite timeout" in captured.err
    assert "make test" in captured.err
    assert "ACTION REQUIRED FOR AI AGENTS" in captured.err


def test_main_rejects_positional_arguments(tmp_path: Path) -> None:
    runner = StubRunner([])

    try:
        main(["unexpected"], runner=runner, cwd=tmp_path)
    except SystemExit as exc:
        assert str(exc) == "ralph.verify does not accept positional arguments"
    else:
        raise AssertionError("expected SystemExit")

    assert runner.calls == []
