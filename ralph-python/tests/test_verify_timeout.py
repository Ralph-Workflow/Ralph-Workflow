from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from ralph.runtime import (
    DEFAULT_TEST_TIMEOUT_SECONDS,
    TEST_TIMEOUT_ENV,
    SuiteTimeoutError,
    build_timeout_env,
    run_command_with_timeout,
    timeout_seconds_from_env,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_timeout_seconds_from_env_uses_default_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TEST_TIMEOUT_ENV, raising=False)

    assert timeout_seconds_from_env(TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS) == 1.0


def test_build_timeout_env_sets_timeout_values() -> None:
    env = build_timeout_env(
        base_env={"A": "B"}, test_timeout_seconds=1.0, suite_timeout_seconds=10.0
    )

    assert env["A"] == "B"
    assert env[TEST_TIMEOUT_ENV] == "1.0"
    assert env["RALPH_PYTEST_SUITE_TIMEOUT_SECONDS"] == "10.0"


def test_run_command_with_timeout_returns_completed_process(tmp_path: Path) -> None:
    result = run_command_with_timeout(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        suite_timeout_seconds=1.0,
    )

    assert result.returncode == 0
    assert result.stdout == "ok\n"


def test_run_command_with_timeout_raises_on_suite_timeout(tmp_path: Path) -> None:
    with pytest.raises(SuiteTimeoutError, match=r"exceeded 0\.1 seconds"):
        run_command_with_timeout(
            [sys.executable, "-c", "import time; time.sleep(1)"],
            cwd=tmp_path,
            suite_timeout_seconds=0.1,
        )
