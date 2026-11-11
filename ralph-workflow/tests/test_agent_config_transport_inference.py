from __future__ import annotations

from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig


def test_agy_cmd_infers_agy_transport() -> None:
    config = AgentConfig(cmd="agy", json_parser=JsonParserType.GENERIC)

    assert config.transport == AgentTransport.AGY


def test_opencode_cmd_still_infers_opencode_transport() -> None:
    config = AgentConfig(cmd="opencode", json_parser=JsonParserType.OPENCODE)

    assert config.transport == AgentTransport.OPENCODE


def test_claude_cmd_infers_claude_interactive_transport() -> None:
    config = AgentConfig(cmd="claude", json_parser=JsonParserType.GENERIC)

    assert config.transport == AgentTransport.CLAUDE_INTERACTIVE


def test_codex_cmd_infers_codex_transport() -> None:
    config = AgentConfig(cmd="codex", json_parser=JsonParserType.CODEX)

    assert config.transport == AgentTransport.CODEX


def test_explicit_transport_overrides_inference() -> None:
    config = AgentConfig(
        cmd="agy",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.OPENCODE,
    )

    assert config.transport == AgentTransport.OPENCODE


def test_nanocoder_cmd_infers_nanocoder_transport() -> None:
    config = AgentConfig(cmd="nanocoder", json_parser=JsonParserType.GENERIC)

    assert config.transport == AgentTransport.NANOCODER


def test_pi_cmd_infers_pi_transport() -> None:
    config = AgentConfig(cmd="pi", json_parser=JsonParserType.PI)

    assert config.transport == AgentTransport.PI


def test_pi_cmd_with_generic_parser_infers_pi_transport() -> None:
    config = AgentConfig(cmd="pi", json_parser=JsonParserType.GENERIC)

    assert config.transport == AgentTransport.PI
