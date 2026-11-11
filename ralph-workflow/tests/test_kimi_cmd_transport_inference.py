from __future__ import annotations

from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig


def test_kimi_cmd_infers_kimi_transport() -> None:
    config = AgentConfig(cmd="kimi", json_parser=JsonParserType.GENERIC)

    assert config.transport is AgentTransport.KIMI


def test_existing_cmd_inference_entries_are_unchanged() -> None:
    expected = {
        "claude": AgentTransport.CLAUDE_INTERACTIVE,
        "codex": AgentTransport.CODEX,
        "opencode": AgentTransport.OPENCODE,
        "nanocoder": AgentTransport.NANOCODER,
        "agy": AgentTransport.AGY,
        "pi": AgentTransport.PI,
    }
    for cmd, transport in expected.items():
        config = AgentConfig(cmd=cmd, json_parser=JsonParserType.GENERIC)
        assert config.transport is transport, f"cmd={cmd!r} inferred {config.transport!r}"


def test_unknown_cmd_still_falls_through_to_generic() -> None:
    config = AgentConfig(cmd="something-else", json_parser=JsonParserType.GENERIC)

    assert config.transport is AgentTransport.GENERIC


def test_absolute_kimi_path_infers_kimi_transport() -> None:
    config = AgentConfig(cmd="/usr/local/bin/kimi", json_parser=JsonParserType.GENERIC)

    assert config.transport is AgentTransport.KIMI
