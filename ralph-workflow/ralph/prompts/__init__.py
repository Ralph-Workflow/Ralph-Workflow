
from __future__ import annotations

from typing import cast

_TEMPLATE_VARIABLE_EXPORTS = frozenset(
    {
        "CapabilitySet",
        "PolicyFlag",
        "PolicyFlagSet",
        "SessionCapabilities",
        "capability_template_variables",
        "capability_template_variables_from_session",
        "default_caps_and_flags_for_drain",
        "visible_mcp_tool_names",
    }
)


def __getattr__(name: str) -> object:
    if name not in _TEMPLATE_VARIABLE_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    from . import template_variables

    namespace = cast("dict[str, object]", template_variables.__dict__)
    value = namespace[name]
    cast("dict[str, object]", globals())[name] = value
    return value


__all__ = [
    "CapabilitySet",
    "PolicyFlag",
    "PolicyFlagSet",
    "SessionCapabilities",
    "capability_template_variables",
    "capability_template_variables_from_session",
    "default_caps_and_flags_for_drain",
    "visible_mcp_tool_names",
]
