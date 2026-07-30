"""Prompt capability variables, flag sets, and rendering support.

This package exposes the capability types and variable builders used by phase
handlers when they materialize agent-facing Jinja templates from
``ralph/prompts/templates/``.

Main entry points:

- ``capability_template_variables(capabilities, flags)`` — builds the template variable
  dict for a given ``CapabilitySet`` and ``PolicyFlagSet``. Used when rendering prompts
  that reference capability gates.
- ``capability_template_variables_from_session(session)`` — convenience wrapper that
  extracts capabilities and flags from a live ``SessionCapabilities`` object.
- ``default_caps_and_flags_for_drain(drain_class)`` — returns the default capability
  set and policy flags for a drain class; used for prompt preview and testing.
- ``visible_mcp_tool_names(session)`` — returns the list of MCP tool names visible to
  the agent, based on its granted capabilities.
- ``CapabilitySet``, ``PolicyFlag``, ``PolicyFlagSet`` — typed sets for capability and
  policy-flag resolution.
- ``SessionCapabilities`` — the per-session capability snapshot passed in from the MCP
  server startup.

For template rendering, context building, and payload materialization,
see ``ralph.prompts.materialize`` and ``ralph.prompts.template_engine``.
"""

from __future__ import annotations

from .template_variables import (
    CapabilitySet,
    PolicyFlag,
    PolicyFlagSet,
    SessionCapabilities,
    capability_template_variables,
    capability_template_variables_from_session,
    default_caps_and_flags_for_drain,
    visible_mcp_tool_names,
)

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
