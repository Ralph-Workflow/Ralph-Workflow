from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.catalog import AgentCatalog, default_catalog
    from ralph.agents.chain import AgentChain
    from ralph.agents.invoke import invoke_agent
    from ralph.agents.registration import register_agent_support, register_my_agent
    from ralph.agents.registry import AgentRegistry
    from ralph.agents.support import AgentSupport

__all__ = [
    "AgentCatalog",
    "AgentChain",
    "AgentRegistry",
    "AgentSupport",
    "default_catalog",
    "invoke_agent",
    "register_agent_support",
    "register_my_agent",
]


def _load_agentcatalog() -> object:
    from ralph.agents.catalog import AgentCatalog
    return AgentCatalog

def _load_agentchain() -> object:
    from ralph.agents.chain import AgentChain
    return AgentChain

def _load_agentregistry() -> object:
    from ralph.agents.registry import AgentRegistry
    return AgentRegistry

def _load_agentsupport() -> object:
    from ralph.agents.support import AgentSupport
    return AgentSupport

def _load_default_catalog() -> object:
    from ralph.agents.catalog import default_catalog
    return default_catalog

def _load_invoke_agent() -> object:
    from ralph.agents.invoke import invoke_agent
    return invoke_agent

def _load_register_agent_support() -> object:
    from ralph.agents.registration import register_agent_support
    return register_agent_support

def _load_register_my_agent() -> object:
    from ralph.agents.registration import register_my_agent
    return register_my_agent

_LOADERS: dict[str, Callable[[], object]] = {
    "AgentCatalog": _load_agentcatalog,
    "AgentChain": _load_agentchain,
    "AgentRegistry": _load_agentregistry,
    "AgentSupport": _load_agentsupport,
    "default_catalog": _load_default_catalog,
    "invoke_agent": _load_invoke_agent,
    "register_agent_support": _load_register_agent_support,
    "register_my_agent": _load_register_my_agent,
}


def __getattr__(name: str) -> object:
    loader = _LOADERS.get(name)
    if loader is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return loader()
