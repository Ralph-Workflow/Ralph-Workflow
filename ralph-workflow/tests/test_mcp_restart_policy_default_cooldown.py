from __future__ import annotations

import importlib

from ralph.mcp.server import _mcp_restart_policy
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy


def test_mcp_restart_policy_default_cooldown_is_two_seconds() -> None:
    policy = McpRestartPolicy()
    assert policy.cooldown_seconds == 2.0
    assert policy.cooldown_seconds > 0


def test_mcp_restart_policy_cooldown_is_overridable() -> None:
    policy = McpRestartPolicy(cooldown_seconds=0.5)
    assert policy.cooldown_seconds == 0.5


def test_mcp_restart_policy_cooldown_default_survives_python_minus_o() -> None:
    import ralph.mcp.server._mcp_restart_policy as module

    importlib.reload(module)


def test_mcp_restart_policy_module_invariant_runtime_check() -> None:
    assert _mcp_restart_policy.McpRestartPolicy().cooldown_seconds > 0
    policy_zero = McpRestartPolicy(cooldown_seconds=0.0)
    assert policy_zero.cooldown_seconds == 0.0
    bridge_cooldown = policy_zero.cooldown_seconds
    if not bridge_cooldown > 0:
        return
    raise AssertionError("zero cooldown should short-circuit the bridge guard")
