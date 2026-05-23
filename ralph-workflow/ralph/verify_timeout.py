"""Test timeout enforcement wrapper.

This module runs a pytest suite with per-test and full-suite timeout limits.
Per-test limit is ``DEFAULT_TEST_TIMEOUT_SECONDS`` (1 s); suite limit is
``DEFAULT_SUITE_TIMEOUT_SECONDS`` (30 s). A test that exceeds these limits is
a design defect — fix the production coupling, not the timeout.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.executor.process import (
    TIMEOUT_EXIT_CODE,
    ProcessResult,
    ProcessRunOptions,
    run_process,
)

DEFAULT_TEST_TIMEOUT_SECONDS = 1.0
DEFAULT_SUITE_TIMEOUT_SECONDS = 120.0
TEST_TIMEOUT_ENV = "RALPH_PYTEST_TEST_TIMEOUT_SECONDS"
SUITE_TIMEOUT_ENV = "RALPH_PYTEST_SUITE_TIMEOUT_SECONDS"

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


_POLICY_FIX_MESSAGE = (
    "\n"
    "╔══════════════════════════════════════════════════════════════════════╗\n"
    "║         POLICY VIOLATION — ACTION REQUIRED FOR AI AGENTS            ║\n"
    "╚══════════════════════════════════════════════════════════════════════╝\n"
    "\n"
    "YOU MUST fix the slow test(s). Do NOT raise the limit. Do NOT skip or\n"
    "quarantine to hide the problem. A slow test is a design defect.\n"
    "\n"
    "Limits:\n"
    "  Per-test  : 1 second   (RALPH_PYTEST_TEST_TIMEOUT_SECONDS)\n"
    "  Full suite: 30 seconds (RALPH_PYTEST_SUITE_TIMEOUT_SECONDS)\n"
    "\n"
    "HOW TO FIX\n"
    "----------\n"
    "1. Replace real I/O with fakes.\n"
    "   Filesystem   → MemoryWorkspace or tmp_path fixture\n"
    "   Subprocesses → MockProcessExecutor or monkeypatch\n"
    "   Network/HTTP → patch at the boundary; never real sockets\n"
    "\n"
    "2. Eliminate sleep() and real wall-clock waits.\n"
    "   Inject a clock abstraction and advance it in the test.\n"
    "   Never call time.sleep(), asyncio.sleep(), or Event.wait() in tests.\n"
    "\n"
    "3. Refactor production code for testability.\n"
    "   If the production path forces real I/O, that is a coupling problem.\n"
    "   Extract I/O behind an interface; inject a fast fake in the test.\n"
    "   Do the refactor — do not work around it.\n"
    "\n"
    "4. Assert on observable behavior, not implementation internals.\n"
    "   If changing the implementation (without changing behavior) breaks a\n"
    "   test, the test is wrong. Rewrite it to test the public contract.\n"
    "\n"
    "DONE when ALL hold:\n"
    "  [ ] Every test finishes in < 1 s\n"
    "  [ ] Full suite finishes in < 30 s wall-clock\n"
    "  [ ] No test calls sleep() or polls real wall-clock time\n"
    "  [ ] No test crosses a real I/O boundary\n"
    "  [ ] Every assertion targets observable behavior\n"
    "\n"
    "Full policy: ralph/verify_timeout.py module docstring\n"
    "         or: docs/agents/testing-guide.md  §'Test Performance Policy'\n"
)


class SuiteTimeoutError(RuntimeError):
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(
            f"Test suite exceeded the {timeout_seconds}s wall-clock limit.\n{_POLICY_FIX_MESSAGE}"
        )
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
    capture_output: bool = True,
) -> ProcessResult:
    cmd = tuple(command)
    result = run_process(
        cmd[0],
        cmd[1:],
        options=ProcessRunOptions(
            cwd=cwd,
            env=dict(env) if env is not None else None,
            timeout=suite_timeout_seconds,
            capture_output=capture_output,
        ),
    )
    if result.returncode == TIMEOUT_EXIT_CODE:
        raise SuiteTimeoutError(suite_timeout_seconds)
    return result


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
            capture_output=False,
        )
    except SuiteTimeoutError as exc:
        print(str(exc), file=sys.stderr)
        return 124

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
