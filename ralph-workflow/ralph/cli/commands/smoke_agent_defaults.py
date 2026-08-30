"""Resolve smoke-harness agent defaults from the operator's own configuration.

A smoke run is only evidence about the live pipeline when it runs what the
pipeline runs. Each ``smoke-interactive-*`` command used to pin a hardcoded
``<transport>/<provider>/<model>`` alias -- duplicated as a string literal in
``ralph/cli/main.py`` (the value the CLI actually used) and again in the
command function's own signature (shadowed, so the two could silently
disagree). The operator's pipeline is driven by ``[agent_chains]`` in
``~/.config/ralph-workflow.toml``, so a pinned smoke alias exercised a model
the pipeline never ran, and went stale the moment the provider retired that
model id.

This module is the SINGLE source of truth for those defaults. When the
operator passes no ``--agent``, the smoke command resolves the default from
the operator's own chains.

Resolution order (deterministic, and documented here because the choice is
observable):

1. Walk ``config.agent_chains`` in **operator-config declaration order** --
   the order the chains appear in the operator's TOML, preserved by
   ``tomllib`` and pydantic.
2. Within each chain, walk ``chain.agents`` in **fallback order**; entry
   zero is the agent the pipeline actually runs for that chain, the rest
   are its fallbacks.
3. Return the first alias that resolves to the requested transport.
4. When the operator's chains name no alias for that transport, fall back to
   the **bare transport alias** (e.g. ``"opencode"``). A bare alias passes no
   ``--model``, so the agent CLI uses the model the operator configured for
   it -- a default that cannot go stale.

An explicit ``--agent`` always wins; this resolver is consulted only when the
operator supplied none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.builtin import builtin_supports
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.config.models import AgentConfig, UnifiedConfig

__all__ = [
    "CONFIG_ALIAS_DEFAULT_SMOKE_COMMANDS",
    "SMOKE_COMMAND_TRANSPORTS",
    "bare_transport_alias",
    "resolve_default_smoke_agent",
]


#: Every ``smoke-interactive-*`` CLI command whose ``--agent`` default is
#: resolved from the operator's ``[agent_chains]``, keyed by CLI command name.
#: A new transport that grows a smoke command MUST be added here; the parity
#: test fails on any ``--agent``-taking smoke command missing from this table.
SMOKE_COMMAND_TRANSPORTS: Mapping[str, AgentTransport] = {
    "smoke-interactive-agy": AgentTransport.AGY,
    "smoke-interactive-codex": AgentTransport.CODEX,
    "smoke-interactive-cursor": AgentTransport.CURSOR,
    "smoke-interactive-kimi": AgentTransport.KIMI,
    "smoke-interactive-nanocoder": AgentTransport.NANOCODER,
    "smoke-interactive-opencode": AgentTransport.OPENCODE,
    "smoke-interactive-pi": AgentTransport.PI,
}

#: Smoke commands whose ``--agent`` default names an operator-defined alias
#: namespace rather than a transport. ``ccs/<alias>`` entries come from the
#: operator's ``[ccs_aliases]`` table and every one of them resolves to the
#: Claude transport, so a transport-keyed chain lookup cannot pick the right
#: one. Their defaults name no provider or model, so they cannot go stale the
#: way a pinned ``<transport>/<provider>/<model>`` literal does.
CONFIG_ALIAS_DEFAULT_SMOKE_COMMANDS: tuple[str, ...] = ("smoke-interactive-ccs",)


def bare_transport_alias(transport: AgentTransport) -> str:
    """Return the bare built-in agent alias for ``transport``.

    Single-sourced from the built-in agent registry rather than re-spelling
    the transport's alias, so a renamed built-in cannot drift from the smoke
    default. Falls back to the transport's own enum value when no built-in
    claims the transport.

    Args:
        transport: The agent transport whose bare alias is wanted.

    Returns:
        The bare alias (e.g. ``"opencode"``) that passes no ``--model``.
    """
    for support in builtin_supports():
        if support.transport is transport:
            return support.name
    return transport.value


def resolve_default_smoke_agent(
    transport: AgentTransport,
    config: UnifiedConfig,
    lookup: Callable[[str], AgentConfig | None],
) -> str:
    """Return the alias the operator's own configuration would run for ``transport``.

    See the module docstring for the (deliberate, observable) resolution
    order.

    Args:
        transport: The transport the smoke command drives.
        config: The operator's loaded configuration.
        lookup: Alias resolver -- normally ``AgentRegistry.from_config(config).get``
            -- so a dynamic ``<transport>/<model>`` alias resolves exactly the
            way the pipeline resolves it.

    Returns:
        The first configured chain alias that resolves to ``transport``, or
        the bare transport alias when the operator's chains name none.
    """
    for chain in config.agent_chains.values():
        for alias in chain.agents:
            agent_config = lookup(alias)
            if agent_config is not None and agent_config.transport is transport:
                return alias
    return bare_transport_alias(transport)
