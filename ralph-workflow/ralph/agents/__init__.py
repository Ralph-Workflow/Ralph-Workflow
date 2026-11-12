from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.agents.catalog import AgentCatalog, default_catalog
    from ralph.agents.chain import AgentChain
    from ralph.agents.invoke import invoke_agent
    from ralph.agents.registration import register_agent_support, register_my_agent
    from ralph.agents.registry import AgentRegistry
    from ralph.agents.support import AgentSupport

__doc__ = "register_my_agent is the 90% recipe for agent registration."

__all__ = [
    "AgentCatalog",
    "AgentChain",
    "AgentRegistry",
    "AgentSupport",
    "default_catalog",
    "invoke_agent",
    "register_my_agent",
    "register_agent_support",
]

__doc__ = "register_my_agent"


def __getattr__(name: str) -> object:
    exports = {
        "AgentCatalog": ("ralph.agents.catalog", "AgentCatalog"),
        "AgentChain": ("ralph.agents.chain", "AgentChain"),
        "AgentRegistry": ("ralph.agents.registry", "AgentRegistry"),
        "AgentSupport": ("ralph.agents.support", "AgentSupport"),
        "default_catalog": ("ralph.agents.catalog", "default_catalog"),
        "invoke_agent": ("ralph.agents.invoke", "invoke_agent"),
        "register_agent_support": ("ralph.agents.registration", "register_agent_support"),
        "register_my_agent": ("ralph.agents.registration", "register_my_agent"),
    }
    try:
        module_name, attr_name = exports[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    module = import_module(module_name)
    namespace = cast("dict[str, object]", module.__dict__)
    value = namespace[attr_name]
    cast("dict[str, object]", globals())[name] = value
    return value
