"""Resolve smoke-harness agent defaults from the effective agents policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.chain import ChainManager
from ralph.agents.drain_not_bound_error import DrainNotBoundError
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.config.models import AgentConfig
    from ralph.policy.models import AgentsPolicy, DrainName

__all__ = [
    "CONFIG_ALIAS_DEFAULT_SMOKE_COMMANDS",
    "SMOKE_COMMAND_DRAINS",
    "SMOKE_COMMAND_TRANSPORTS",
    "resolve_default_smoke_agent",
]


SMOKE_COMMAND_TRANSPORTS: Mapping[str, AgentTransport] = {
    "smoke-interactive-agy": AgentTransport.AGY,
    "smoke-interactive-codex": AgentTransport.CODEX,
    "smoke-interactive-cursor": AgentTransport.CURSOR,
    "smoke-interactive-kimi": AgentTransport.KIMI,
    "smoke-interactive-nanocoder": AgentTransport.NANOCODER,
    "smoke-interactive-opencode": AgentTransport.OPENCODE,
    "smoke-interactive-pi": AgentTransport.PI,
    "smoke-interactive-ccs": AgentTransport.CLAUDE,
    "smoke-interactive-claude": AgentTransport.CLAUDE_INTERACTIVE,
    "smoke-headless-claude": AgentTransport.CLAUDE,
}

SMOKE_COMMAND_DRAINS: Mapping[str, DrainName] = dict.fromkeys(
    SMOKE_COMMAND_TRANSPORTS, "development"
)

CONFIG_ALIAS_DEFAULT_SMOKE_COMMANDS: tuple[str, ...] = ()


def resolve_default_smoke_agent(
    transport: AgentTransport,
    agents_policy: AgentsPolicy,
    lookup: Callable[[str], AgentConfig | None],
    *,
    drain: DrainName,
    command_prefix: str | None = None,
) -> str | None:
    """Select a compatible policy-chain alias or return ``None`` for an actionable CLI failure.

    The caller owns presenting the failure because only it knows which smoke
    command's explicit ``--agent`` override is applicable.
    """
    try:
        chain = ChainManager(agents_policy).chain_for_drain(drain)
    except DrainNotBoundError:
        return None
    for alias in chain.agents:
        agent_config = lookup(alias)
        if (
            agent_config is not None
            and agent_config.transport is transport
            and (command_prefix is None or agent_config.cmd.startswith(command_prefix))
        ):
            return alias
    return None
