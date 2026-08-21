"""Which identity a session serving SEVERAL agents may claim.

One session is built before anyone knows which agent in a chain will
run, so the rules here decide what that session may say about the CLI
and the provider on the other end. Both resolvers use them -- the
fan-out phase session and the commit chain -- which is the whole reason
they live in one module: the same rule split across two call sites, with
only one of them updated, has been the defect in this area more than
once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.registry import AgentRegistry
from ralph.mcp.multimodal.capabilities import (
    select_session_transport,
    session_transport_is_ambiguous,
    transport_inline_image_roundtrip_unsafe,
)

if TYPE_CHECKING:
    from ralph.config.enums import AgentTransport
    from ralph.config.models import UnifiedConfig


def phase_session_identity(
    first_agent_transport: AgentTransport | None,
    first_agent_model_flag: str | None,
    chain_transport: AgentTransport | None,
    *,
    chain_is_ambiguous: bool = False,
) -> tuple[AgentTransport | None, str | None]:
    """Return the (transport, model_flag) pair to tag a phase session with.

    The model flag belongs to the FIRST candidate agent. Once the
    session's tag names a different CLI, the two no longer describe the
    same agent, and pairing them resolves a provider for a model the
    tagged CLI is not running -- which turns pdf/document delivery into
    a hard unsupported error for the very agent the flag came from. So
    the flag is dropped and only the safe tag is carried.

    The reset also fires when the restricted candidate happens to be
    FIRST. The tag then equals the flag's own agent, so an identity test
    alone skipped the reset and resolved a provider for the whole chain
    -- making the same mixed chain give a capable agent a usable
    reference or a hard error depending only on chain order.

    A ``chain_transport`` of ``None`` means "no restricted candidate and
    no agreement", and the original transport is KEPT: it also selects
    the native upstream MCP loaders, and clearing it silently dropped a
    mixed chain's upstream server discovery for the whole session. No
    candidate is restricted in that case, so the delivery guard does not
    care which of the agreeing-on-nothing transports the tag names.

    Public because it is the whole decision: as a private branch inside
    the plan builder it could be reverted with the suite green.
    """
    if chain_is_ambiguous:
        # CHECKED FIRST, before anything looks at the transport. This
        # test used to live inside the ``chain_transport is None``
        # branch alone -- so the case it was written for never reached
        # it: candidates that AGREE on the CLI and disagree on the model
        # resolve a non-None transport, fell past it, and kept the first
        # agent's flag verbatim. The flag was computed and discarded,
        # which is worse than not having added it, because the commit
        # said the hole was closed.
        #
        # The transport is still carried (it also selects the native
        # upstream MCP loaders); only the provider claim is dropped,
        # degrading delivery to resource references every candidate can
        # accept.
        return chain_transport or first_agent_transport, None
    if chain_transport is None:
        # Nothing was resolved and the candidates do not disagree (that
        # case returned above): there is no restricted candidate, so the
        # first agent's own pairing stands.
        return first_agent_transport, first_agent_model_flag
    if chain_transport is not first_agent_transport or transport_inline_image_roundtrip_unsafe(
        chain_transport.value
    ):
        return chain_transport, None
    return first_agent_transport, first_agent_model_flag


def chain_disagrees_on_model(model_flags: list[str]) -> bool:
    """True when the chain's candidates do not all name the same model.

    Public because BOTH chain resolvers need it -- the fan-out phase
    session and the commit chain. The commit one delegated to the
    transport rule alone, which is the same "one rule, two call sites"
    split this work keeps finding.

    A phase session carries ONE model flag, read from the first
    candidate. If the candidates disagree, that flag describes an agent
    that may not be the one that runs, and pairing it with the session
    resolves that agent's provider for every delivery decision in the
    phase.
    """
    if len(model_flags) < _MIN_CANDIDATES_TO_DISAGREE:
        return False
    first = model_flags[0]
    return any(flag != first for flag in model_flags)


#: Two candidates are the fewest that can disagree about anything.
_MIN_CANDIDATES_TO_DISAGREE = 2


def resolve_phase_session_transport(
    candidate_agents: list[str],
    config: UnifiedConfig | None,
) -> tuple[AgentTransport | None, bool]:
    """Return the transport to tag a phase session serving ``candidate_agents``.

    Returns ``(transport, ambiguous)``. The transport is ``None`` when
    there is no honest answer -- no candidates, no config, or a mixed
    chain of unrestricted agents. Falling back to the first candidate
    would reinstate the guess this helper replaces, and on the
    no-candidate paths the caller's own transport is provably ``None``
    anyway.

    ``ambiguous`` separates "the candidates DISAGREE" from "there was
    nothing to go on". Both produce no transport, but only the first
    means the first agent's model flag describes a CLI that may not be
    the one that runs.
    """
    if config is None or not candidate_agents:
        return None, False
    registry = AgentRegistry.from_config(config)
    by_value: dict[str, AgentTransport] = {}
    ordered: list[str] = []
    model_flags: list[str] = []
    for name in candidate_agents:
        cfg = registry.get(name)
        if cfg is None or cfg.transport is None:
            continue
        ordered.append(cfg.transport.value)
        by_value[cfg.transport.value] = cfg.transport
        model_flags.append((cfg.model_flag or "").strip())
    selected = select_session_transport(ordered)
    # Disagreement about the MODEL counts too. Comparing transports
    # alone called a chain unambiguous when its candidates shared a CLI
    # but named different providers -- two ``claude``-transport agents
    # where only the first carries ``--model gemini/...`` -- so the
    # session resolved GEMINI's capabilities for the whole phase and
    # minted AudioContent and VideoContent that the agent which
    # actually ran cannot carry. That is the hazard this flag exists
    # for, and it was order-dependent in exactly the way it criticises.
    ambiguous = session_transport_is_ambiguous(ordered) or chain_disagrees_on_model(model_flags)
    if selected is None:
        return None, ambiguous
    return by_value.get(selected), ambiguous




__all__ = [
    "chain_disagrees_on_model",
    "phase_session_identity",
    "resolve_phase_session_transport",
]
