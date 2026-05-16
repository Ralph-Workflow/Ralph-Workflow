"""Public agent-management exports.

This package exposes the small set of agent abstractions most callers need:
registry lookup, chain composition, and process invocation.

Imports are resolved lazily so submodule imports like ``ralph.agents.clock`` do
not pull in the full agent runtime during package initialization.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.chain import AgentChain
    from ralph.agents.invoke import invoke_agent
    from ralph.agents.registry import AgentRegistry

__all__ = ["AgentChain", "AgentRegistry", "invoke_agent"]


def __getattr__(name: str) -> object:
    exports = {
        "AgentChain": ("ralph.agents.chain", "AgentChain"),
        "AgentRegistry": ("ralph.agents.registry", "AgentRegistry"),
        "invoke_agent": ("ralph.agents.invoke", "invoke_agent"),
    }
    try:
        module_name, attr_name = exports[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
