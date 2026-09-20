from __future__ import annotations

from pathlib import Path

from ralph.testing.audit_mcp_agent_boundary import audit_source


def test_scoped_reset_positive_and_negative_snippets() -> None:
    assert audit_source(
        Path("session_bridge.py"),
        "def scoped_reset_tool_registry_callback(scope_key):\n    return scope_key\n",
    ) == []
    violations = audit_source(
        Path("session_bridge.py"),
        "def reset_tool_registry_callback():\n    return None\n",
    )

    assert len(violations) == 1
    assert violations[0].line == 1
    assert "unscoped reset wrapper" in str(violations[0])


def test_typed_e2big_positive_and_negative_snippets() -> None:
    assert audit_source(
        Path("_process_manager.py"),
        "try:\n    run()\nexcept OSError as exc:\n    if exc.errno == errno.E2BIG:\n        raise AgentLaunchError('agent', exc, 1)\n",
    ) == []
    violations = audit_source(
        Path("_process_manager.py"),
        "try:\n    run()\nexcept OSError as exc:\n    if exc.errno == errno.E2BIG:\n        raise exc\n",
    )

    assert len(violations) == 1
    assert violations[0].line == 3
    assert "typed E2BIG" in str(violations[0])


def test_issuer_bearing_teardown_positive_and_negative_snippets() -> None:
    assert audit_source(
        Path("ralph/agents/invoke/reader.py"),
        "teardown_subtree(pid, issuer='watchdog')\n",
    ) == []
    violations = audit_source(
        Path("ralph/agents/invoke/reader.py"), "teardown_subtree(pid)\n"
    )

    assert len(violations) == 1
    assert violations[0].line == 1
    assert "teardown needs issuer" in str(violations[0])


def test_watchdog_positive_and_negative_snippets() -> None:
    assert audit_source(
        Path("ralph/agents/invoke/reader.py"),
        "IdleWatchdogKilledError('idle', 15, issuer='watchdog', runtime_event='mcp_operation')\n",
    ) == []
    violations = audit_source(
        Path("ralph/agents/invoke/reader.py"),
        "IdleWatchdogKilledError('idle', 15)\n",
    )

    assert len(violations) == 1
    assert violations[0].line == 1
    assert "runtime_event" in str(violations[0])
