"""Unit tests for the built-in agent supports tuple."""

from __future__ import annotations

from ralph.agents.builtin import builtin_supports
from ralph.agents.registry import builtin_agents


def test_builtin_supports_properties() -> None:
    # (a) builtin_supports() returns 6 entries
    supports = builtin_supports()
    assert len(supports) == 6

    # (b) every name and every cmd is unique
    names = [s.name for s in supports]
    cmds = [s.cmd for s in supports]
    assert len(set(names)) == 6, f"Duplicate names found: {names}"
    assert len(set(cmds)) == 6, f"Duplicate cmds found: {cmds}"

    # (c) every entry has a non-None parser_factory, strategy_factory, and spec
    for s in supports:
        assert s.parser_factory is not None
        assert s.strategy_factory is not None
        assert s.spec is not None

    # (d) every interactive entry (claude, agy) has spec.requires_pty is True, other 4 False
    for s in supports:
        if s.name in {"claude", "agy"}:
            assert s.spec.requires_pty is True, f"{s.name} should require PTY"
        else:
            assert s.spec.requires_pty is False, f"{s.name} should not require PTY"

    # (e) the 6 names are exactly {claude, claude-headless, codex, opencode, nanocoder, agy}
    expected_names = {"claude", "claude-headless", "codex", "opencode", "nanocoder", "agy"}
    assert set(names) == expected_names

    # (f) the 6 cmd values match the current builtin_agents() dict exactly
    legacy_agents = builtin_agents()
    for s in supports:
        assert s.name in legacy_agents
        assert s.cmd == legacy_agents[s.name].cmd
        assert s.config.transport == legacy_agents[s.name].transport
