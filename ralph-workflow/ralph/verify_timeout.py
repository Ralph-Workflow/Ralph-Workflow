"""Test timeout enforcement wrapper.

This module runs a pytest suite with per-test and full-suite timeout limits.
Per-test limit is ``DEFAULT_TEST_TIMEOUT_SECONDS`` (1 s); suite limit is
``DEFAULT_SUITE_TIMEOUT_SECONDS`` (60 s). A test that exceeds these limits is
a design defect — fix the production coupling, not the timeout.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from ralph.executor.process import TIMEOUT_EXIT_CODE, ProcessResult, ProcessRunOptions, run_process
from ralph.process.manager import ProcessManager, ProcessManagerPolicy

DEFAULT_TEST_TIMEOUT_SECONDS: Final = 1.0
DEFAULT_SUITE_TIMEOUT_SECONDS: Final = 60.0
TEST_TIMEOUT_ENV = "RALPH_PYTEST_TEST_TIMEOUT_SECONDS"
SUITE_TIMEOUT_ENV = "RALPH_PYTEST_SUITE_TIMEOUT_SECONDS"
_VERIFY_TIMEOUT_PM = ProcessManager(policy=ProcessManagerPolicy(log_events=False))

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
    "  Per-test                         : 1 second   (RALPH_PYTEST_TEST_TIMEOUT_SECONDS)\n"
    "  Per-suite invocation             : 60 seconds (RALPH_PYTEST_SUITE_TIMEOUT_SECONDS)\n"
    "  ALL test suites combined (total): 60 seconds\n"
    "    (ABSOLUTE and IMMUTABLE — enforced by ralph/verify.py\n"
    "     _TOTAL_TEST_BUDGET_SECONDS = 60.0, tracked\n"
    "     cumulatively via time.monotonic())\n"
    "\n"
    "These limits are ABSOLUTE. You CANNOT avoid them by:\n"
    "- Splitting tests into more suites (adds process overhead,\n"
    "  risks combined total breach, tracked cumulatively\n"
    "  by ralph/verify.py — N suites does NOT give N x 60s)\n"
    "- Moving slow tests to a different suite or target\n"
    "- Raising DEFAULT_SUITE_TIMEOUT_SECONDS\n"
    "  (this is exactly the violation committed\n"
    "   — do NOT repeat it)\n"
    "- Changing PYTEST_SUITE_TIMEOUT_SECONDS in the Makefile\n"
    "The combined wall-clock time of ALL suites\n"
    "  running sequentially must stay within 60s\n"
    "  when make verify is run.\n"
    "A slow test is a design defect—fix the production coupling,\n"
    "  not the timeout.\n"
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
    "  [ ] Full suite finishes in < 60 s wall-clock\n"
    "  [ ] No test calls sleep() or polls real wall-clock time\n"
    "  [ ] No test crosses a real I/O boundary\n"
    "  [ ] Every assertion targets observable behavior\n"
    "  [ ] Splitting into more suites does not increase total budget\n"
    "\n"
    "Full policy: ralph/verify_timeout.py module docstring\n"
    "         or: docs/agents/testing-guide.md  §'Test Performance Policy'\n"
)


class SuiteTimeoutError(RuntimeError):
    """Raised when a pytest invocation exceeds the configured suite timeout.

    Args:
        timeout_seconds: The wall-clock cap (in seconds) that the
            subprocess exceeded before ``run_process`` reported
            ``TIMEOUT_EXIT_CODE``. Surfaced as ``self.timeout_seconds``
            for programmatic inspection.

    The error message embeds the policy-violation banner from
    ``_POLICY_FIX_MESSAGE`` so the agent sees the full fix guidance
    on first sight.
    """

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(
            f"Test suite exceeded the {timeout_seconds}s wall-clock limit.\n{_POLICY_FIX_MESSAGE}"
        )
        self.timeout_seconds = timeout_seconds


def timeout_seconds_from_env(name: str, default: float) -> float:
    """Read a timeout value from the process environment.

    Args:
        name: Environment variable name. Recognised values include
            ``RALPH_PYTEST_TEST_TIMEOUT_SECONDS`` and
            ``RALPH_PYTEST_SUITE_TIMEOUT_SECONDS``.
        default: Value returned when ``name`` is unset.

    Returns:
        The parsed float from the environment, or ``default`` if the
        variable is missing. Raises ``ValueError`` if the variable is
        set but not parseable as a float.
    """
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
    """Build a subprocess environment carrying the per-test and per-suite timeouts.

    Args:
        base_env: Environment mapping to copy. When ``None``, the
            current ``os.environ`` is used as the base.
        test_timeout_seconds: Value for ``RALPH_PYTEST_TEST_TIMEOUT_SECONDS``
            (default ``DEFAULT_TEST_TIMEOUT_SECONDS`` = 1.0).
        suite_timeout_seconds: Value for ``RALPH_PYTEST_SUITE_TIMEOUT_SECONDS``
            (default ``DEFAULT_SUITE_TIMEOUT_SECONDS`` = 60.0).

    Returns:
        A fresh dict containing every base entry plus the two
        timeout variables. Caller-owned: mutations do not affect
        ``os.environ`` or the base mapping.
    """
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
    """Run ``command`` under the bounded subprocess manager with a suite timeout.

    Args:
        command: The argv to invoke. ``command[0]`` is the executable.
        cwd: Working directory for the subprocess.
        env: Environment mapping for the subprocess. ``None`` inherits
            the parent process environment.
        suite_timeout_seconds: Wall-clock cap (seconds) passed to
            ``run_process``. Default is ``DEFAULT_SUITE_TIMEOUT_SECONDS``
            (60 s). Note this is the per-invocation cap; the combined
            test budget is enforced upstream in ``ralph.verify``.
        capture_output: When ``True`` (default), capture stdout/stderr
            into the returned ``ProcessResult``; when ``False``, the
            subprocess writes directly to the parent's streams.

    Returns:
        The ``ProcessResult`` from ``run_process``.

    Raises:
        SuiteTimeoutError: When the subprocess exits with
            ``TIMEOUT_EXIT_CODE``, indicating the suite exceeded
            ``suite_timeout_seconds``.

    Side effects:
        Spawns a subprocess through the shared ``_VERIFY_TIMEOUT_PM``
        ``ProcessManager``. The subprocess inherits the parent
        environment (with the timeout env vars added when ``env`` is
        ``None``); both stdout/stderr are routed per ``capture_output``.
    """
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
        _pm=_VERIFY_TIMEOUT_PM,
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
    """Run ``argv`` as a subprocess under the configured suite timeout.

    Returns the subprocess returncode, or 124 on ``SuiteTimeoutError``
    (matching the conventional ``timeout(1)`` exit code).
    """
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
