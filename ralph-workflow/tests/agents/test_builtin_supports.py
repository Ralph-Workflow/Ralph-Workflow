from __future__ import annotations

from ralph.agents.builtin import builtin_supports
from ralph.agents.registry import builtin_agents


def test_builtin_supports_properties() -> None:
    supports = builtin_supports()
    assert len(supports) == 9

    names = [s.name for s in supports]
    cmds = [s.cmd for s in supports]
    assert len(set(names)) == 9, f"Duplicate names found: {names}"
    assert len(set(cmds)) == 9, f"Duplicate cmds found: {cmds}"

    for s in supports:
        assert s.parser_factory is not None
        assert s.strategy_factory is not None
        assert s.spec is not None

    for s in supports:
        if s.name in {"claude", "nanocoder", "agy"}:
            assert s.spec.requires_pty is True, f"{s.name} should require PTY"
        else:
            assert s.spec.requires_pty is False, f"{s.name} should not require PTY"

    expected_names = {
        "claude",
        "claude-headless",
        "codex",
        "opencode",
        "nanocoder",
        "agy",
        "pi",
        "cursor",
        "kimi",
    }
    assert set(names) == expected_names

    legacy_agents = builtin_agents()
    for s in supports:
        assert s.name in legacy_agents
        assert s.cmd == legacy_agents[s.name].cmd
        assert s.config.transport == legacy_agents[s.name].transport
