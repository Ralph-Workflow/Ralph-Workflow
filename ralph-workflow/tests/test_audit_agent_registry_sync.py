"""Unit tests for the agent registry sync static audit."""

from __future__ import annotations

from ralph.testing.audit_agent_registry_sync import (
    audit_builtin_file,
    audit_invoke_file,
    audit_registry_file,
)


def test_audit_builtin_violations() -> None:
    # Test builtins without _BUILTIN_AGENT_SUPPORTS tuple
    violations = audit_builtin_file("x = 1", "test_builtin.py")
    assert len(violations) >= 1
    assert violations[0].category == "missing_constant"

    # Test duplicates and invalid entries
    code = """
_BUILTIN_AGENT_SUPPORTS = (
    AgentSupport.from_registration_kwargs("claude", cmd="claude"),
    AgentSupport.from_registration_kwargs("claude", cmd="claude"),
)
"""
    violations = audit_builtin_file(code, "test_builtin.py")
    assert any(v.category == "duplicate_names" for v in violations)
    assert any(v.category == "duplicate_cmds" for v in violations)


def test_audit_registry_violations() -> None:
    # Test missing unregister and missing seed
    code = """
class AgentRegistry:
    def __init__(self):
        pass
    def from_config(self):
        pass
def builtin_agents():
    return {}
"""
    violations = audit_registry_file(code, "test_registry.py")
    assert any(v.category == "missing_unregister" for v in violations)
    assert any(v.category == "missing_seed_call" for v in violations)
    assert any(v.category == "non_derived_view" for v in violations)


def test_audit_invoke_violations() -> None:
    # Test missing requires_pty ladder
    code = """
def invoke_agent():
    pass
"""
    violations = audit_invoke_file(code, "test_invoke.py")
    assert any(v.category == "invalid_dispatch_ladder" for v in violations)

    # Test legacy CLAUDE_INTERACTIVE or AGY branches
    code = """
def invoke_agent():
    if requires_pty:
        run_pty()
    else:
        run_subprocess_and_read_lines()

    if transport == AgentTransport.CLAUDE_INTERACTIVE:
        pass
"""
    violations = audit_invoke_file(code, "test_invoke.py")
    assert any(v.category == "legacy_fallback_ladder" for v in violations)
