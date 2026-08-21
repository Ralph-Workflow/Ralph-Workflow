"""A phase session must not pair a model flag with another agent's CLI.

One phase session serves every agent in a chain, but it is built before
the chain is walked. The model flag read at that point belongs to the
FIRST candidate; the session's transport tag is resolved across ALL of
them, conservatively. When those two name different CLIs, pairing them
resolves a provider for a model the tagged CLI is not running -- and
that turns pdf/document delivery into a hard unsupported error for the
very agent the flag came from.

The reset had no test at all: reverting it to a plain identity check
survived a targeted 4887-test sweep.
"""

from __future__ import annotations

from ralph.config.enums import AgentTransport
from ralph.pipeline.fan_out import phase_session_identity

_FLAG = "--model gpt-5.6-terra"


def test_a_restricted_chain_transport_drops_the_first_agents_model_flag() -> None:
    """The tag names codex; the flag names the first agent's model."""
    transport, model_flag = phase_session_identity(
        AgentTransport.CLAUDE, _FLAG, AgentTransport.CODEX
    )

    assert transport is AgentTransport.CODEX
    assert model_flag is None


def test_the_reset_fires_even_when_the_restricted_agent_is_first() -> None:
    """Chain ORDER must not decide whether the guard runs.

    When the restricted candidate is first, the resolved tag equals the
    flag's own agent, so an identity test alone skipped the reset. The
    same mixed chain then gave a capable agent a usable reference or a
    hard error depending only on which agent was listed first.
    """
    transport, model_flag = phase_session_identity(
        AgentTransport.CODEX, _FLAG, AgentTransport.CODEX
    )

    assert transport is AgentTransport.CODEX
    assert model_flag is None


def test_an_unrestricted_agreeing_chain_keeps_both() -> None:
    """Nothing is restricted, so the flag still describes the tagged CLI."""
    transport, model_flag = phase_session_identity(
        AgentTransport.CLAUDE, _FLAG, AgentTransport.CLAUDE
    )

    assert transport is AgentTransport.CLAUDE
    assert model_flag == _FLAG


def test_an_unresolved_chain_keeps_the_original_transport() -> None:
    """``None`` means no restricted candidate and no agreement.

    The transport also selects the native upstream MCP loaders, so
    clearing it here silently dropped a mixed chain's upstream server
    discovery for the whole session.
    """
    transport, model_flag = phase_session_identity(AgentTransport.CLAUDE, _FLAG, None)

    assert transport is AgentTransport.CLAUDE
    assert model_flag == _FLAG
