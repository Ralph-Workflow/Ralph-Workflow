"""Unit tests for the agent module state static audit."""

from __future__ import annotations

import textwrap
from pathlib import Path

from ralph.testing.audit_agent_module_state import (
    _FORBIDDEN_NAME_PREFIXES,
    _name_violates,
    _scan_file,
    run_audit,
)


def test_forbidden_prefix_includes_legacy_module_state_names() -> None:
    """The audit must watch for the 3 historical module-level state prefixes."""
    assert "_PARSER_REGISTRY" in _FORBIDDEN_NAME_PREFIXES
    assert "_CUSTOM_COMMAND_REGISTRY" in _FORBIDDEN_NAME_PREFIXES
    assert "_STRATEGY_DISPATCH" in _FORBIDDEN_NAME_PREFIXES


def test_name_violates_matches_forbidden_prefix() -> None:
    """A name starting with a forbidden prefix is a violation."""
    for prefix in _FORBIDDEN_NAME_PREFIXES:
        assert _name_violates(f"{prefix}_DATA"), f"{prefix}_DATA must be flagged"
        assert _name_violates(prefix), f"{prefix} must be flagged"


def test_name_violates_matches_registry_plus_agent_or_parser() -> None:
    """A name containing both 'registry' and ('agent' or 'parser') is a violation."""
    assert _name_violates("AGENT_REGISTRY")
    assert _name_violates("agent_registry_dict")
    assert _name_violates("PARSER_REGISTRY")
    assert _name_violates("parser_registry_table")
    assert _name_violates("_AGENT_PARSER_REGISTRY")
    assert not _name_violates("registry")
    assert not _name_violates("agent")
    assert not _name_violates("parser")
    assert not _name_violates("agent_table")
    assert not _name_violates("registry_table")


def test_clean_module_passes_scan() -> None:
    """A module without forbidden dict assignments has zero violations."""
    content = textwrap.dedent(
        """
        CONSTANT = 42
        ANOTHER_CONSTANT = "hello"
        from typing import TYPE_CHECKING
        """
    )
    violations = _scan_file(content, "ralph/agents/clean.py")
    assert violations == []


def test_parser_registry_dict_assignment_is_violation() -> None:
    """A ``_PARSER_REGISTRY = {...}`` module-level dict assignment is flagged."""
    content = textwrap.dedent(
        """
        _PARSER_REGISTRY = {
            "claude": ClaudeParser,
        }
        """
    )
    violations = _scan_file(content, "ralph/agents/bad.py")
    assert any(v.category == "module_level_state" for v in violations)
    assert any("PARSER_REGISTRY" in v.detail for v in violations)


def test_strategy_dispatch_dict_assignment_is_violation() -> None:
    """A ``_STRATEGY_DISPATCH = {...}`` module-level dict assignment is flagged."""
    content = textwrap.dedent(
        """
        _STRATEGY_DISPATCH = {
            "claude": ClaudeStrategy,
        }
        """
    )
    violations = _scan_file(content, "ralph/agents/bad.py")
    assert any(v.category == "module_level_state" for v in violations)
    assert any("STRATEGY_DISPATCH" in v.detail for v in violations)


def test_custom_command_registry_dict_assignment_is_violation() -> None:
    """A ``_CUSTOM_COMMAND_REGISTRY = {...}`` assignment is flagged."""
    content = textwrap.dedent(
        """
        _CUSTOM_COMMAND_REGISTRY = {
            "cmd": entry,
        }
        """
    )
    violations = _scan_file(content, "ralph/agents/bad.py")
    assert any(v.category == "module_level_state" for v in violations)


def test_parser_registry_entry_class_not_flagged() -> None:
    """A ``class _PARSER_REGISTRY_ENTRY`` class declaration is NOT a violation."""
    content = textwrap.dedent(
        """
        class _PARSER_REGISTRY_ENTRY:
            pass
        """
    )
    violations = _scan_file(content, "ralph/agents/clean.py")
    assert violations == []


def test_mapping_proxy_type_view_not_flagged() -> None:
    """A ``_PARSER_REGISTRY = types.MappingProxyType(...)`` is NOT a dict assignment."""
    content = textwrap.dedent(
        """
        import types
        _PARSER_REGISTRY = types.MappingProxyType({})
        """
    )
    violations = _scan_file(content, "ralph/agents/clean.py")
    assert violations == []


def test_function_local_dict_not_flagged() -> None:
    """A dict assignment inside a function is NOT a module-level violation."""
    content = textwrap.dedent(
        """
        def helper():
            _PARSER_REGISTRY = {"claude": ClaudeParser}
            return _PARSER_REGISTRY
        """
    )
    violations = _scan_file(content, "ralph/agents/clean.py")
    assert violations == []


def test_class_attribute_dict_not_flagged() -> None:
    """A dict assignment inside a class body is NOT a module-level violation."""
    content = textwrap.dedent(
        """
        class Foo:
            _PARSER_REGISTRY = {"claude": ClaudeParser}
        """
    )
    violations = _scan_file(content, "ralph/agents/clean.py")
    assert violations == []


def test_frozenset_assignment_not_flagged() -> None:
    """A frozenset assignment with a forbidden prefix is NOT a violation (not a dict)."""
    content = textwrap.dedent(
        """
        _PARSER_REGISTRY_PREFIXES = frozenset({"claude"})
        """
    )
    violations = _scan_file(content, "ralph/agents/clean.py")
    assert violations == []


def test_agent_registry_class_not_flagged() -> None:
    """A module-level class named ``AgentRegistry`` is NOT a violation (not a dict)."""
    content = textwrap.dedent(
        """
        class AgentRegistry:
            pass
        """
    )
    violations = _scan_file(content, "ralph/agents/clean.py")
    assert violations == []


def test_run_audit_against_repo_finds_no_violations() -> None:
    """Running the audit against the current repo must find zero violations.

    This is the integration check: after the refactor, no module-level
    dict with the forbidden names should exist under ralph/agents/.
    """
    package_root = Path(__file__).resolve().parent.parent
    violations = run_audit(package_root)
    assert violations == [], (
        f"audit_agent_module_state found unexpected violations: {violations}"
    )
