"""Compatibility re-export of the verify-timeout policy from ``ralph.verify_timeout``.

This module makes ``ralph.runtime.verify_timeout`` a stable documented surface
for callers that import through the ``ralph.runtime`` namespace. All public
symbols are re-exported from ``ralph.verify_timeout`` unchanged.

.. important::

    The 60-second ABSOLUTE and IMMUTABLE combined test budget for ``make verify``
    is enforced by ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`` via cumulative
    ``time.monotonic()`` tracking across ALL test steps. Per-suite timeouts in
    this module are SECONDARY caps only — raising them does not increase the
    combined budget. Splitting tests across N suites does NOT give N x 60 s.
"""

from ralph.verify_timeout import (
    DEFAULT_SUITE_TIMEOUT_SECONDS,
    DEFAULT_TEST_TIMEOUT_SECONDS,
    SUITE_TIMEOUT_ENV,
    TEST_TIMEOUT_ENV,
    SuiteTimeoutError,
    build_timeout_env,
    main,
    run_command_with_timeout,
    timeout_seconds_from_env,
)

__all__ = [
    "DEFAULT_SUITE_TIMEOUT_SECONDS",
    "DEFAULT_TEST_TIMEOUT_SECONDS",
    "SUITE_TIMEOUT_ENV",
    "TEST_TIMEOUT_ENV",
    "SuiteTimeoutError",
    "build_timeout_env",
    "main",
    "run_command_with_timeout",
    "timeout_seconds_from_env",
]
