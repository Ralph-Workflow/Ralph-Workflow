from __future__ import annotations

from pathlib import Path

from ralph.testing.audit_mcp_agent_boundary import audit_source


def test_unscoped_reset_wrapper_is_rejected() -> None:
    violations = audit_source(
        Path("session_bridge.py"),
        "def reset_tool_registry_callback():\n    return None\n",
    )

    assert len(violations) == 1
    assert "unscoped reset wrapper" in str(violations[0])


def test_watchdog_without_causal_keywords_is_rejected() -> None:
    violations = audit_source(
        Path("ralph/agents/invoke/reader.py"),
        "IdleWatchdogKilledError('idle', 15)\n",
    )

    assert len(violations) == 1
    assert "runtime_event" in str(violations[0])
