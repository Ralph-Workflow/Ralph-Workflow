"""Tests for ExecutionError message rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools._exec_run_deps import ExecRunDeps
from ralph.mcp.tools.coordination import ToolContent
from ralph.mcp.tools.exec import (
    PROCESS_EXEC_BOUNDED_CAPABILITY,
    _CompletedProcessAdapter,
    handle_exec_command,
)
from ralph.process.manager import DeadlineTimeoutExpired
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot


def test_cache_full_message_describes_automatic_reset_without_internal_tool() -> None:
    err = ExecutionError(
        "exec cache exceeds hard cap even after automatic reset",
        current_bytes=500,
        cap_bytes=200,
        removed_paths=3,
        removed_bytes=0,
        remaining_bytes=500,
        diagnostics="current=500 cap=200 removed=3 active_slots=1 attributed=300",
    )
    message = str(err)
    assert "automatic" in message.lower() or "reset" in message.lower(), (
        "cache-full message must describe automatic reset attempt"
    )
    assert (
        "active" in message.lower() or "live" in message.lower() or "permission" in message.lower()
    ), "cache-full message must explain why bytes remain (active slots / permissions)"
    assert "unacquirable locks" not in message, (
        "cache-full message must not contain lock-era wording"
    )
    assert "Cleanup+reset" not in message, (
        "cache-full message must not use old Cleanup+reset wording"
    )
    assert "cooldown" not in message.lower(), "cache-full message must not contain cooldown wording"
    assert "active_leases" not in message, (
        "cache-full message must not contain old lock-era active_leases field"
    )


def test_timeout_message_presents_both_interpretations() -> None:
    """A timeout is ambiguous: the command may be legitimately long (raise the
    limit) OR genuinely stuck (infinite loop / deadlock / blocked on input),
    where raising the limit only wastes more time. The message must surface BOTH
    so the agent does not reflexively double the timeout on a wedged command."""
    err = ExecutionError(
        "Failed to execute 'x': timed out after 90000ms",
        timed_out=True,
        timeout_ms=90_000,
        suggested_timeout_ms=180_000,
    )
    message = str(err).lower()
    # Interpretation 1: legitimately long -> raise the limit.
    assert "timeout_ms" in message
    assert "180000" in message
    # Interpretation 2: the command itself may be broken and must be fixed.
    assert any(word in message for word in ("loop", "stuck", "hang", "deadlock"))


def test_hard_cap_timeout_message_tells_agent_to_split_or_background_work() -> None:
    err = ExecutionError(
        timed_out=True,
        timeout_ms=90_000,
        timeout_cause="hard_cap",
    )

    message = str(err).lower()
    assert "ceiling" in message
    assert "split" in message
    assert "background" in message
    assert "pass a larger timeout_ms" not in message


def test_inactivity_timeout_message_explains_output_resets_the_window() -> None:
    err = ExecutionError(
        timed_out=True,
        timeout_ms=90_000,
        timeout_cause="inactivity",
        suggested_timeout_ms=180_000,
    )

    message = str(err).lower()
    assert "no output" in message
    assert "resets" in message
    assert "180000" in message


def test_timeout_message_without_suggestion_still_warns_about_stuck_commands() -> None:
    err = ExecutionError(
        "Failed to execute 'x': timed out",
        timed_out=True,
        timeout_ms=0,
    )
    message = str(err).lower()
    assert any(word in message for word in ("loop", "stuck", "hang", "deadlock"))


@pytest.mark.parametrize(
    ("timeout_ms", "timeout_cause", "expected_elapsed_ms", "expected_suggestion"),
    [
        (EXEC_MAX_TIMEOUT_MS, "hard_cap", EXEC_MAX_TIMEOUT_MS, None),
        (5_000, "inactivity", 5_000, 10_000),
    ],
)
def test_run_command_timeout_reports_the_expired_deadline_and_useful_suggestion(
    tmp_path: Path,
    timeout_ms: int,
    timeout_cause: Literal["hard_cap", "inactivity"],
    expected_elapsed_ms: int,
    expected_suggestion: int | None,
) -> None:
    def timeout_runner(
        _argv: list[str], _cwd: Path, timeout: float | None
    ) -> _CompletedProcessAdapter:
        raise DeadlineTimeoutExpired(
            ["slow"], timeout or 1, timeout_cause=timeout_cause
        )

    result = handle_exec_command(
        MockSession({PROCESS_EXEC_BOUNDED_CAPABILITY}),
        MockWorkspaceRoot(tmp_path),
        {"command": "slow", "timeout_ms": timeout_ms},
        deps=ExecRunDeps(runner=timeout_runner),
    )

    content = result.content[0]
    assert result.is_error is True
    assert isinstance(content, ToolContent)
    assert f"after {expected_elapsed_ms}ms" in content.text
    if expected_suggestion is None:
        assert "Suggested timeout_ms" not in content.text
    else:
        assert f"Suggested timeout_ms: {expected_suggestion}." in content.text
