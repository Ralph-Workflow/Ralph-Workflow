"""Test helpers for Ralph Workflow.

This package exports fake subprocess and process-management helpers for unit tests,
along with timeout management utilities for keeping the test suite within the 60-second
wall-clock budget.

Main entry points:

- ``FakeAsyncProcess``, ``FakeControllableAsyncProcess``, ``FakePopen``,
  ``FakeStubbornPopen``, ``FakeImmortalPopen``, ``FakeTimeoutPopen`` — in-memory subprocess
  fakes for testing agent invocation without spawning real processes.
- ``FakePsutil``, ``FakePsutilProcess``, ``make_psutil_factory`` — psutil stubs for
  testing process-liveness logic.
- ``make_async_process_factory``, ``make_sync_process_factory`` — factory helpers that
  inject fakes into callers under test.
- ``run_command_with_timeout``, ``timeout_seconds_from_env``, ``build_timeout_env`` —
  subprocess execution with enforced wall-clock limits sourced from
  ``RALPH_TEST_TIMEOUT_SECONDS`` and ``RALPH_SUITE_TIMEOUT_SECONDS``.
- ``SuiteTimeoutError`` — raised when a test suite exceeds its timeout budget.
- ``DEFAULT_TEST_TIMEOUT_SECONDS``, ``DEFAULT_SUITE_TIMEOUT_SECONDS`` — default caps.

Import directly from this package rather than from sub-modules:

    from ralph.testing import FakePopen, run_command_with_timeout
"""

from ralph.testing.fake_process import (
    FakeAsyncProcess,
    FakeControllableAsyncProcess,
    FakeImmortalPopen,
    FakePopen,
    FakePsutil,
    FakePsutilProcess,
    FakeStubbornPopen,
    FakeTimeoutPopen,
    make_async_process_factory,
    make_psutil_factory,
    make_sync_process_factory,
)
from ralph.verify_timeout import (
    DEFAULT_SUITE_TIMEOUT_SECONDS,
    DEFAULT_TEST_TIMEOUT_SECONDS,
    SUITE_TIMEOUT_ENV,
    TEST_TIMEOUT_ENV,
    SuiteTimeoutError,
    build_timeout_env,
    run_command_with_timeout,
    timeout_seconds_from_env,
)

__all__ = [
    "DEFAULT_SUITE_TIMEOUT_SECONDS",
    "DEFAULT_TEST_TIMEOUT_SECONDS",
    "SUITE_TIMEOUT_ENV",
    "TEST_TIMEOUT_ENV",
    "FakeAsyncProcess",
    "FakeControllableAsyncProcess",
    "FakeImmortalPopen",
    "FakePopen",
    "FakePsutil",
    "FakePsutilProcess",
    "FakeStubbornPopen",
    "FakeTimeoutPopen",
    "SuiteTimeoutError",
    "build_timeout_env",
    "make_async_process_factory",
    "make_psutil_factory",
    "make_sync_process_factory",
    "run_command_with_timeout",
    "timeout_seconds_from_env",
]
