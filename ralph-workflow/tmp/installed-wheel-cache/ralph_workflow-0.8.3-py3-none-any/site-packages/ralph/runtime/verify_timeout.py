"""Compatibility re-export of the verify-timeout policy from ``ralph.verify_timeout``.

This module makes ``ralph.runtime.verify_timeout`` a stable documented surface
for callers that import through the ``ralph.runtime`` namespace. All public
symbols are re-exported from ``ralph.verify_timeout`` unchanged.
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
