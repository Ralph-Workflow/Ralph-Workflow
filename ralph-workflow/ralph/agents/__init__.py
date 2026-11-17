from __future__ import annotations

from collections.abc import Callable

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def _load_AgentCatalog() -> object:
    from ralph.agents.catalog import AgentCatalog
    return AgentCatalog

def _load_AgentChain() -> object:
    from ralph.agents.chain import AgentChain
    return AgentChain

def _load_AgentRegistry() -> object:
    from ralph.agents.registry import AgentRegistry
    return AgentRegistry

def _load_AgentSupport() -> object:
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
    "AgentCatalog": _load_AgentCatalog,
    "AgentChain": _load_AgentChain,
    "AgentRegistry": _load_AgentRegistry,
    "AgentSupport": _load_AgentSupport,
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
