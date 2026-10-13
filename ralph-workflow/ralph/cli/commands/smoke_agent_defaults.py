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

_BARE_AGENT_BY_TRANSPORT: Mapping[AgentTransport, str] = {
    AgentTransport.AGY: "agy",
    AgentTransport.CLAUDE: "claude-headless",
    AgentTransport.CLAUDE_INTERACTIVE: "claude",
    AgentTransport.CODEX: "codex",
    AgentTransport.CURSOR: "cursor",
    AgentTransport.KIMI: "kimi",
    AgentTransport.NANOCODER: "nanocoder",
    AgentTransport.OPENCODE: "opencode",
    AgentTransport.PI: "pi",
}


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
        chain = None
    if chain is not None:
        for alias in chain.agents:
            agent_config = lookup(alias)
            if (
                agent_config is not None
                and agent_config.transport is transport
                and (command_prefix is None or agent_config.cmd.startswith(command_prefix))
            ):
                return alias
    bare_alias = _BARE_AGENT_BY_TRANSPORT.get(transport)
    if bare_alias is None:
        return None
    bare_config = lookup(bare_alias)
    if bare_config is None or (
        command_prefix is not None and not bare_config.cmd.startswith(command_prefix)
    ):
        return None
    return bare_alias
