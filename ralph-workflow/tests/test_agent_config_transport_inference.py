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
