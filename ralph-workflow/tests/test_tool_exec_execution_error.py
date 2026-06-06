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
        diagnostics="total=500 bytes, pools=1, active_leases=1, cooldown=no",
    )
    message = str(err)
    assert "automatic" in message.lower() or "reset" in message.lower(), (
        "cache-full message must describe automatic reset attempt"
    )
    assert (
        "active" in message.lower()
        or "live" in message.lower()
        or "permission" in message.lower()
    ), "cache-full message must explain why bytes remain (active slots / permissions)"


def test_cleanup_cooldown_message_describes_recovery_steps() -> None:
    err = ExecutionError(
        "exec cache cleanup has failed repeatedly",
        consecutive_failures=5,
        cooldown_remaining_s=250.0,
        last_error="permission denied",
    )
    message = str(err)
    assert "cooldown" in message.lower() or "wait" in message.lower(), (
        "cooldown message must explain the cooldown situation"
    )
    assert (
        "active" in message.lower()
        or "live" in message.lower()
        or "slot" in message.lower()
    ), "cooldown message must mention active/live exec slots as a possible cause"
