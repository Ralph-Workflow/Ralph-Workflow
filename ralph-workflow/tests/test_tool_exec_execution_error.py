"""Tests for ExecutionError message rendering."""

from __future__ import annotations

from ralph.mcp.tools._exec_execution_error import ExecutionError


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


def test_timeout_message_without_suggestion_still_warns_about_stuck_commands() -> None:
    err = ExecutionError(
        "Failed to execute 'x': timed out",
        timed_out=True,
        timeout_ms=0,
    )
    message = str(err).lower()
    assert any(word in message for word in ("loop", "stuck", "hang", "deadlock"))
