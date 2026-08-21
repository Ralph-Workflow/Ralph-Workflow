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


def test_a_mixed_unrestricted_chain_drops_the_model_flag_too() -> None:
    """Disagreement is not the same as knowing nothing.

    ``select_session_transport`` answers ``None`` both when there is
    nothing to go on and when the candidates DISAGREE. Collapsing the
    two kept the first agent's model flag for a mixed chain, so the
    phase session resolved THAT agent's provider and minted typed blocks
    -- a PdfContent for a chain whose fallback is opencode, Audio and
    VideoContent for one whose fallback is claude -- that the agent
    which actually ran cannot carry. The transport is still kept,
    because it also selects the native upstream MCP loaders.
    """
    transport, model_flag = phase_session_identity(
        AgentTransport.CLAUDE, _FLAG, None, chain_is_ambiguous=True
    )

    assert transport is AgentTransport.CLAUDE
    assert model_flag is None


def test_ambiguity_is_computed_from_the_candidates() -> None:
    """The predicate the fan-out passes must agree with the chains it sees.

    Both chains below are documented verbatim in the shipped
    ``ralph-workflow.toml`` as fallback examples.
    """
    from ralph.mcp.multimodal.capabilities import (
        select_session_transport,
        session_transport_is_ambiguous,
    )

    assert session_transport_is_ambiguous(["claude", "opencode"]) is True
    assert session_transport_is_ambiguous(["agy", "claude"]) is True
    # A homogeneous chain has an honest answer, so it is not ambiguous.
    assert session_transport_is_ambiguous(["claude", "claude"]) is False
    assert session_transport_is_ambiguous(["claude"]) is False
    assert session_transport_is_ambiguous([]) is False
    # A restricted candidate RESOLVES the chain; that is not ambiguity,
    # and treating it as such would drop the tag the guards need.
    assert session_transport_is_ambiguous(["claude", "codex"]) is False
    assert select_session_transport(["claude", "codex"]) == "codex"
