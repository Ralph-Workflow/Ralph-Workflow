"""Run the maintained pytest verification suite under the current interpreter.

.. note::

    The 60-second ABSOLUTE and IMMUTABLE combined test budget is enforced
    UPSTREAM by ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`` via cumulative
    ``time.monotonic()`` tracking, not by this module. This module provides
    per-suite timeout wrapping only. Splitting tests into more suites or
    adding new test targets does NOT increase the combined budget.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.verify_timeout import (
    DEFAULT_SUITE_TIMEOUT_SECONDS,
    DEFAULT_TEST_TIMEOUT_SECONDS,
    TEST_TIMEOUT_ENV,
    build_timeout_env,
    run_command_with_timeout,
    timeout_seconds_from_env,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Protocol

    from ralph.executor.process import ProcessResult

    class SuiteRunner(Protocol):
        def __call__(
            self,
            command: Sequence[str],
            *,
            cwd: Path,
            env: Mapping[str, str] | None = None,
            suite_timeout_seconds: float,
        ) -> ProcessResult: ...


# Default worker count leaves two logical cores free for the main process and
# system overhead. On a 12-core machine this uses 10 workers; pytest-xdist's
# ``auto`` can over-subscribe and cause per-test timeouts under load, so we
# cap the default. Override via PYTEST_WORKERS env var.
_DEFAULT_PYTEST_WORKERS = "auto"


def _pytest_workers() -> str:
    env = os.getenv("PYTEST_WORKERS", _DEFAULT_PYTEST_WORKERS)
    if env == "auto":
        cores = os.cpu_count() or 1
        return str(max(1, cores - 2))
    return env


def _default_runner(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    suite_timeout_seconds: float,
) -> ProcessResult:
    return run_command_with_timeout(
        command,
        cwd=cwd,
        env=env,
        suite_timeout_seconds=suite_timeout_seconds,
        capture_output=False,
    )


def _verification_command() -> tuple[str, ...]:
    workers = _pytest_workers()
    return (
        sys.executable,
        "-m",
        "pytest",
        "tests/",
        "-q",
        "-n",
        workers,
        "--dist",
        "worksteal",
        "-m",
        "not subprocess_e2e",
    )


def run_test_suites(
    *,
    cwd: Path,
    suite_timeout_seconds: float = DEFAULT_SUITE_TIMEOUT_SECONDS,
    runner: SuiteRunner = _default_runner,
) -> int:
    env = build_timeout_env(
        test_timeout_seconds=timeout_seconds_from_env(
            TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS
        ),
        suite_timeout_seconds=suite_timeout_seconds,
    )
    result = runner(
        _verification_command(),
        cwd=cwd,
        env=env,
        suite_timeout_seconds=suite_timeout_seconds,
    )
    return result.returncode


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        raise SystemExit("ralph.test_suites does not accept positional arguments")
    return run_test_suites(cwd=Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
