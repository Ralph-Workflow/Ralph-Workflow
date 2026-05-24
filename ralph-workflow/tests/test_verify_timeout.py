from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from ralph.executor.process import ProcessRunOptions, run_process
from ralph.runtime import (
    DEFAULT_TEST_TIMEOUT_SECONDS,
    SUITE_TIMEOUT_ENV,
    TEST_TIMEOUT_ENV,
    SuiteTimeoutError,
    build_timeout_env,
    run_command_with_timeout,
    timeout_seconds_from_env,
)

TIMEOUT_EXCEEDED_SECONDS = 0.05
SLOW_COMMAND_SECONDS = 0.2
RAW_PYTEST_TIMEOUT_EXIT_CODE = 124
RAW_PYTEST_MAX_ELAPSED_SECONDS = 4.0


def test_timeout_seconds_from_env_uses_default_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TEST_TIMEOUT_ENV, raising=False)

    assert timeout_seconds_from_env(TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS) == 1.0


def test_build_timeout_env_sets_timeout_values() -> None:
    env = build_timeout_env(
        base_env={"A": "B"}, test_timeout_seconds=1.0, suite_timeout_seconds=30.0
    )

    assert env["A"] == "B"
    assert env[TEST_TIMEOUT_ENV] == "1.0"
    assert env["RALPH_PYTEST_SUITE_TIMEOUT_SECONDS"] == "30.0"


def test_run_command_with_timeout_returns_completed_process(tmp_path: Path) -> None:
    result = run_command_with_timeout(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        suite_timeout_seconds=1.0,
    )

    assert result.returncode == 0
    assert result.stdout == "ok\n"


def test_run_command_with_timeout_raises_on_suite_timeout(tmp_path: Path) -> None:
    with pytest.raises(
        SuiteTimeoutError,
        match=rf"exceeded the {TIMEOUT_EXCEEDED_SECONDS}s wall-clock limit",
    ):
        run_command_with_timeout(
            [sys.executable, "-c", f"import time; time.sleep({SLOW_COMMAND_SECONDS})"],
            cwd=tmp_path,
            suite_timeout_seconds=TIMEOUT_EXCEEDED_SECONDS,
        )


def test_suite_timeout_error_message_cites_policy() -> None:
    err = SuiteTimeoutError(30.0)
    message = str(err)
    assert "POLICY VIOLATION" in message
    assert "YOU MUST fix" in message
    assert "ralph/verify_timeout.py" in message


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_raw_pytest_run_is_hard_capped_by_suite_timeout(tmp_path: Path) -> None:
    # Write the slow test to the tests directory so conftest.py (which loads
    # the suite watchdog plugin) is properly loaded by pytest during discovery.
    # When pytest runs directly on a file in tmp_path, conftest.py is not loaded
    # and the suite watchdog plugin is not activated.
    tests_dir = Path(__file__).resolve().parents[0]
    slow_test = tests_dir / "test_slow_suite_timeout.py"
    slow_test.write_text(
        "from __future__ import annotations\n\n"
        "import time\n\n\n"
        "def test_sleeps_past_suite_timeout() -> None:\n"
        "    time.sleep(2.0)\n",
        encoding="utf-8",
    )
    try:
        start = time.monotonic()
        result = run_process(
            sys.executable,
            ["-m", "pytest", "-c", "../pytest.ini", "-q", "test_slow_suite_timeout.py"],
            options=ProcessRunOptions(
                cwd=tests_dir,
                env={
                    SUITE_TIMEOUT_ENV: "0.2",
                    TEST_TIMEOUT_ENV: "5.0",
                },
                timeout=15.0,
            ),
        )
        elapsed = time.monotonic() - start

        # Exit code 124 confirms the suite watchdog was activated and killed the process
        assert result.returncode == RAW_PYTEST_TIMEOUT_EXIT_CODE
        # The elapsed time should be less than the max, confirming hard cap
        assert elapsed < RAW_PYTEST_MAX_ELAPSED_SECONDS
        # Note: The "wall-clock limit" message may not appear in stderr because
        # os._exit() bypasses Python's buffer flushing. Exit code 124 is the
        # reliable indicator that the suite watchdog fired.
    finally:
        # Clean up the test file we created
        slow_test.unlink(missing_ok=True)
