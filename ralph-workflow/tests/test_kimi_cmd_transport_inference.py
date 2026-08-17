"""Kimi cmd -> transport inference via ``command_to_transport`` (PA-005).

The built-in kimi entry sets its transport explicitly, but a user-authored
``AgentConfig(cmd="kimi", ...)`` relies on the ``command_to_transport``
inference table.  The ``"kimi"`` entry keeps custom kimi configs from
falling through to ``GENERIC`` (which would silently drop MCP wiring).
"""

from __future__ import annotations

from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig


def test_kimi_cmd_infers_kimi_transport() -> None:
    """``AgentConfig(cmd='kimi')`` infers ``AgentTransport.KIMI``."""
    config = AgentConfig(cmd="kimi", json_parser=JsonParserType.GENERIC)

    assert config.transport is AgentTransport.KIMI


def test_existing_cmd_inference_entries_are_unchanged() -> None:
    """Regression snapshot: the pre-existing inference table is intact."""
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
    """An unlisted command keeps the GENERIC fallback (no silent over-matching)."""
    config = AgentConfig(cmd="something-else", json_parser=JsonParserType.GENERIC)

    assert config.transport is AgentTransport.GENERIC


def test_absolute_kimi_path_falls_through_to_generic() -> None:
    """The table matches the literal first token, not the basename."""
    config = AgentConfig(cmd="/usr/local/bin/kimi", json_parser=JsonParserType.GENERIC)

    assert config.transport is AgentTransport.GENERIC
