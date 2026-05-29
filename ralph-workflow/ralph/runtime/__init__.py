"""Python runtime environment detection and test-timeout utilities.

This package combines two concerns that phase handlers and tests regularly need
together: detecting the Python runtime environment, and managing wall-clock
timeout budgets for test commands.

Main entry points:

- ``detect_runtime_environment()`` — inspects the running Python interpreter and
  returns a ``RuntimeEnvironment`` with version, virtualenv status, and path details.
- ``RuntimeEnvironment`` — structured runtime snapshot (``PythonVersionInfo``,
  virtualenv path, site-packages path).
- ``PythonVersionInfo`` — major, minor, patch version tuple.
- ``is_virtualenv()`` / ``detect_virtualenv_path()`` — virtualenv detection helpers.
- ``run_command_with_timeout``, ``timeout_seconds_from_env``, ``build_timeout_env`` —
  re-exported from ``ralph.verify_timeout``; used by test commands to enforce the
  60-second test-suite budget.
- ``SuiteTimeoutError`` — raised on suite timeout budget exhaustion.
"""

from ralph.runtime._version_info import PythonVersionInfo
from ralph.runtime.environment import (
    RuntimeEnvironment,
    detect_runtime_environment,
    detect_virtualenv_path,
    is_virtualenv,
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
    "PythonVersionInfo",
    "RuntimeEnvironment",
    "SuiteTimeoutError",
    "build_timeout_env",
    "detect_runtime_environment",
    "detect_virtualenv_path",
    "is_virtualenv",
    "run_command_with_timeout",
    "timeout_seconds_from_env",
]
