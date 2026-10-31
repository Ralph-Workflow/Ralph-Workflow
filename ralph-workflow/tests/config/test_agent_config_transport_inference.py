"""Transport inference must recognise the agent binary however ``cmd`` spells it."""

from __future__ import annotations

import pytest

from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ("claude", AgentTransport.CLAUDE_INTERACTIVE),
        ("/Users/me/.local/bin/claude --verbose", AgentTransport.CLAUDE_INTERACTIVE),
        ("env FOO=1 claude", AgentTransport.CLAUDE_INTERACTIVE),
        ("env -u CLAUDECODE FOO=1 /usr/local/bin/claude", AgentTransport.CLAUDE_INTERACTIVE),
        ("FOO=1 codex exec", AgentTransport.CODEX),
        ("/opt/homebrew/bin/codex exec", AgentTransport.CODEX),
        ("'/Applications/My Tools/opencode' run", AgentTransport.OPENCODE),
        ("/usr/bin/env kimi", AgentTransport.KIMI),
        ("my-wrapper claude", AgentTransport.GENERIC),
        ("env", AgentTransport.GENERIC),
        ("claude 'unbalanced", AgentTransport.CLAUDE_INTERACTIVE),
    ],
)
def test_transport_is_inferred_from_the_command_binary_name(
    cmd: str, expected: AgentTransport
) -> None:
    """An absolute path, ``env`` prefix or ``VAR=value`` assignment must not hide the binary."""
    assert AgentConfig(cmd=cmd).transport == expected
