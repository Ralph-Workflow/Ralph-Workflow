from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

DEFAULT_TEST_TIMEOUT_SECONDS = 1.0
DEFAULT_SUITE_TIMEOUT_SECONDS = 10.0
TEST_TIMEOUT_ENV = "RALPH_PYTEST_TEST_TIMEOUT_SECONDS"
SUITE_TIMEOUT_ENV = "RALPH_PYTEST_SUITE_TIMEOUT_SECONDS"

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class SuiteTimeoutError(RuntimeError):
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(f"pytest suite exceeded {timeout_seconds} seconds")
        self.timeout_seconds = timeout_seconds


def timeout_seconds_from_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return float(raw_value)


def build_timeout_env(
    *,
    base_env: Mapping[str, str] | None = None,
    test_timeout_seconds: float = DEFAULT_TEST_TIMEOUT_SECONDS,
    suite_timeout_seconds: float = DEFAULT_SUITE_TIMEOUT_SECONDS,
) -> dict[str, str]:
    env = dict(base_env or os.environ)
    env[TEST_TIMEOUT_ENV] = str(test_timeout_seconds)
    env[SUITE_TIMEOUT_ENV] = str(suite_timeout_seconds)
    return env


def run_command_with_timeout(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    suite_timeout_seconds: float = DEFAULT_SUITE_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd),
            env=dict(env or os.environ),
            text=True,
            check=False,
            capture_output=True,
            timeout=suite_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise SuiteTimeoutError(suite_timeout_seconds) from exc


def _parse_args(argv: Sequence[str]) -> tuple[float, list[str]]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite-timeout", type=float, default=DEFAULT_SUITE_TIMEOUT_SECONDS)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    parsed = parser.parse_args(list(argv))
    suite_timeout = cast("float", parsed.suite_timeout)
    command = cast("list[str]", parsed.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("No command provided")
    return suite_timeout, command


def main(argv: Sequence[str] | None = None) -> int:
    suite_timeout_seconds, command = _parse_args(argv or sys.argv[1:])
    test_timeout_seconds = timeout_seconds_from_env(TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS)
    env = build_timeout_env(
        test_timeout_seconds=test_timeout_seconds,
        suite_timeout_seconds=suite_timeout_seconds,
    )
    try:
        result = run_command_with_timeout(
            command,
            cwd=Path.cwd(),
            env=env,
            suite_timeout_seconds=suite_timeout_seconds,
        )
    except SuiteTimeoutError as exc:
        print(str(exc), file=sys.stderr)
        return 124

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
