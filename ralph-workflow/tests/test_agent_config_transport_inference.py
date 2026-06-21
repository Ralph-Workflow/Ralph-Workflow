"""Tests for agent config transport inference via command_to_transport mapping."""

from __future__ import annotations

from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig


def test_agy_cmd_infers_agy_transport() -> None:
    """AgentConfig(cmd='agy') should infer AgentTransport.AGY via command_to_transport."""
    config = AgentConfig(cmd="agy", json_parser=JsonParserType.GENERIC)

    assert config.transport == AgentTransport.AGY


def test_opencode_cmd_still_infers_opencode_transport() -> None:
    """Regression guard: opencode cmd should still infer OPENCODE transport."""
    config = AgentConfig(cmd="opencode", json_parser=JsonParserType.OPENCODE)

    assert config.transport == AgentTransport.OPENCODE


def test_claude_cmd_infers_claude_interactive_transport() -> None:
    """Regression guard: claude cmd should infer CLAUDE_INTERACTIVE transport.

    Uses GENERIC parser so the parser_to_transport lookup does not short-circuit
    the command_to_transport inference.
    """
    config = AgentConfig(cmd="claude", json_parser=JsonParserType.GENERIC)

    assert config.transport == AgentTransport.CLAUDE_INTERACTIVE


def test_codex_cmd_infers_codex_transport() -> None:
    """Regression guard: codex cmd should infer CODEX transport."""
    config = AgentConfig(cmd="codex", json_parser=JsonParserType.CODEX)

    assert config.transport == AgentTransport.CODEX


def test_explicit_transport_overrides_inference() -> None:
    """When transport is explicitly set, it takes precedence over inference."""
    config = AgentConfig(
        cmd="agy",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.OPENCODE,
    )

    assert config.transport == AgentTransport.OPENCODE


def test_nanocoder_cmd_infers_nanocoder_transport() -> None:
    """AgentConfig(cmd='nanocoder') should infer AgentTransport.NANOCODER."""
    config = AgentConfig(cmd="nanocoder", json_parser=JsonParserType.GENERIC)

    assert config.transport == AgentTransport.NANOCODER


def test_pi_cmd_infers_pi_transport() -> None:
    """AgentConfig(cmd='pi', json_parser=JsonParserType.PI) must infer AgentTransport.PI.

    Regression guard for the gap surfaced by planning analysis: pi was missing
    from both parser_to_transport and command_to_transport maps in
    ralph-workflow/ralph/config/agent_config.py, so the template-style
    AgentConfig(cmd='pi', json_parser=JsonParserType.PI) resolved to
    AgentTransport.GENERIC instead of AgentTransport.PI.
    """
    config = AgentConfig(cmd="pi", json_parser=JsonParserType.PI)

    assert config.transport == AgentTransport.PI


def test_pi_cmd_with_generic_parser_infers_pi_transport() -> None:
    """Regression guard: cmd='pi' with the generic parser must still infer AgentTransport.PI
    via the command_to_transport map (mirrors test_nanocoder_cmd_infers_nanocoder_transport
    which uses JsonParserType.GENERIC to bypass parser_to_transport).
    """
    config = AgentConfig(cmd="pi", json_parser=JsonParserType.GENERIC)

    assert config.transport == AgentTransport.PI
