from __future__ import annotations

from typing import TYPE_CHECKING

from ralph import verify as verify_module
from ralph.executor.process import ProcessResult
from ralph.verify import main

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pytest import CaptureFixture, MonkeyPatch


class StubRunner:
    def __init__(self, results: list[ProcessResult]) -> None:
        self._results = list(results)
        self.calls: list[
            tuple[str, tuple[str, ...], str | Path | None, float | None, bool]
        ] = []

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
        self.calls.append((command, tuple(args), cwd, timeout, capture_output))
        return self._results.pop(0)


def _result(
    *,
    command: tuple[str, ...],
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> ProcessResult:
    return ProcessResult(
        command=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_pytest_commands_keep_makefile_glob_shards_for_helper_only_groups(tmp_path: Path) -> None:
    helper = tmp_path / "tests/test_alpha_helper__fake.py"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text("VALUE = 1\n", encoding="utf-8")

    assert verify_module._pytest_commands(tmp_path) == (
        verify_module._pytest_args("tests/test_[aA]*.py"),
    )


def test_pytest_commands_mirror_makefile_shards_without_expanding_directories(
    tmp_path: Path,
) -> None:
    for path in (
        "tests/agents",
        "tests/config",
        "tests/display",
        "tests/fixtures",
        "tests/unit",
        "tests/mcp",
        "tests/pipeline",
        "tests/recovery",
        "tests/integration",
    ):
        (tmp_path / path).mkdir(parents=True, exist_ok=True)
    for file_path in (
        "tests/test_alpha.py",
        "tests/test_bravo.py",
        "tests/test_qcharlie.py",
        "tests/test_tdelta.py",
        "tests/test_pm_echo.py",
    ):
        path = tmp_path / file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")

    commands = verify_module._pytest_commands(tmp_path)

    assert commands == (
        verify_module._pytest_args(*verify_module._CORE_PATHS),
        verify_module._pytest_args(*verify_module._RUNTIME_PATHS),
        verify_module._pytest_args("tests/test_[aA]*.py"),
        verify_module._pytest_args("tests/test_[bB]*.py"),
        verify_module._pytest_args("tests/test_p[m-zM-Z]*.py"),
        verify_module._pytest_args("tests/test_[q-sQ-S]*.py"),
        verify_module._pytest_args("tests/test_[t-zT-Z]*.py"),
        verify_module._pytest_args(*verify_module._INTEGRATION_PATHS),
    )


def test_main_runs_all_verify_steps_when_successful(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: MonkeyPatch
) -> None:
    pytest_cmd_a = (
        "-m",
        "pytest",
        "tests/agents",
        "tests/config",
        "-q",
        "-n",
        "4",
        "--dist",
        "worksteal",
        "-m",
        "not subprocess_e2e",
    )
    pytest_cmd_b = (
        "-m",
        "pytest",
        "tests/integration/",
        "-q",
        "-n",
        "4",
        "--dist",
        "worksteal",
        "-m",
        "not subprocess_e2e",
    )
    wrapped_a = verify_module._verify_timeout_pytest_args(pytest_cmd_a, timeout=30.0)
    wrapped_b = verify_module._verify_timeout_pytest_args(pytest_cmd_b, timeout=30.0)
    monkeypatch.setattr(
        verify_module,
        "_pytest_commands",
        lambda _cwd: (pytest_cmd_a, pytest_cmd_b),
    )
    runner = StubRunner(
        [
            _result(command=("make", "lint"), returncode=0, stdout="lint ok\n"),
            _result(command=("make", "typecheck"), returncode=0, stdout="typecheck ok\n"),
            _result(command=("uv", *wrapped_a), returncode=0, stdout="test shard a ok\n"),
            _result(command=("uv", *wrapped_b), returncode=0, stdout="test shard b ok\n"),
        ]
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert [call[:2] for call in runner.calls] == [
        ("make", ("lint",)),
        ("make", ("typecheck",)),
        ("uv", wrapped_a),
        ("uv", wrapped_b),
    ]
    assert runner.calls[2][3] is None
    assert runner.calls[2][4] is False
    assert runner.calls[3][3] is None
    assert runner.calls[3][4] is False
    assert "Running full verification..." in captured.out
    assert "ACTION REQUIRED FOR AI AGENTS" not in captured.err


def test_main_prints_agent_fix_banner_when_verify_step_fails(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    runner = StubRunner(
        [
            _result(command=("make", "lint"), returncode=0, stdout="lint ok\n"),
            _result(
                command=("make", "typecheck"),
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


def test_main_wraps_pytest_with_verify_timeout(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    pytest_cmd = (
        "-m",
        "pytest",
        "tests/agents",
        "-q",
        "-n",
        "4",
        "--dist",
        "worksteal",
        "-m",
        "not subprocess_e2e",
    )
    wrapped = verify_module._verify_timeout_pytest_args(pytest_cmd, timeout=30.0)
    monkeypatch.setattr(verify_module, "_pytest_commands", lambda _cwd: (pytest_cmd,))
    runner = StubRunner(
        [
            _result(command=("make", "lint"), returncode=0, stdout="lint ok\n"),
            _result(command=("make", "typecheck"), returncode=0, stdout="typecheck ok\n"),
            _result(command=("uv", *wrapped), returncode=0, stdout="test ok\n"),
        ]
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    assert exit_code == 0
    last_call = runner.calls[-1]
    assert last_call[0] == "uv"
    assert last_call[1] == wrapped
    assert last_call[3] is None
    assert last_call[4] is False


def test_main_treats_no_tests_exit_as_non_failing_shard(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    pytest_cmd = verify_module._pytest_args("tests/test_[aA]*.py")
    wrapped = verify_module._verify_timeout_pytest_args(pytest_cmd, timeout=30.0)
    monkeypatch.setattr(verify_module, "_pytest_commands", lambda _cwd: (pytest_cmd,))
    runner = StubRunner(
        [
            _result(command=("make", "lint"), returncode=0, stdout="lint ok\n"),
            _result(command=("make", "typecheck"), returncode=0, stdout="typecheck ok\n"),
            _result(command=("uv", *wrapped), returncode=5, stdout="no tests ran\n"),
        ]
    )

    exit_code = main([], runner=runner, cwd=tmp_path)

    assert exit_code == 0


def test_suite_timeout_cli_value_avoids_decimal_noise() -> None:
    assert verify_module._suite_timeout_cli_value(30.0) == "30"
    assert verify_module._suite_timeout_cli_value(30.5) == "30.5"
