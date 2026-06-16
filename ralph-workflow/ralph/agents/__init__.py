"""Public agent-management exports.

This package exposes the set of agent abstractions most callers need:
registry lookup, chain composition, process invocation, and support registration.

The unified registration flow enables adding, updating, or removing agents.
For the 90% case, prefer the opinionated 5-line recipe ``register_my_agent``:

    from ralph.agents import register_my_agent, AgentRegistry
    from ralph.agents.parsers.generic import GenericParser
    from ralph.config.enums import AgentTransport

    register_my_agent(
        name="my-agent",
        transport=AgentTransport.GENERIC,
        parser=GenericParser,
        agent_registry=AgentRegistry(),
    )

For advanced scenarios (CCS aliases, dynamic model parsing, custom
``AgentRegistry.ccs_defaults``) use the 14-kwarg ``register_agent_support``
helper or ``AgentCatalog.add`` directly.  Both still delegate to the same
single mutation surface.

Imports are resolved lazily so submodule imports like ``ralph.agents.clock`` do
not pull in the full agent runtime during package initialization.
"""

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

__all__ = [  # noqa: RUF022  # reason: discoverability: register_my_agent (the 90% recipe) must precede register_agent_support
    "AgentCatalog",
    "AgentChain",
    "AgentRegistry",
    "AgentSupport",
    "default_catalog",
    "invoke_agent",
    "register_my_agent",
    "register_agent_support",
]


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
