"""Prompt template utilities: capability variables, flag sets, and template parsing.

This package provides the public surface for building prompt template variables and
parsing prompt template files. It is used by phase handlers to materialise
agent-facing prompts from Jinja2 templates stored under ``ralph/prompts/templates/``.

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
- ``template_parsing`` — module-level re-export of ``ralph.prompts.template_parsing``;
  provides ``parse_template_file`` and related helpers.

For full template rendering (Jinja2 engine, context building, payload materialisation),
see ``ralph.prompts.materialize`` and ``ralph.prompts.template_engine``.
"""

from __future__ import annotations

from . import template_parsing as _template_parsing
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
    "template_parsing",
    "visible_mcp_tool_names",
]

template_parsing = _template_parsing
