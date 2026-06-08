"""Shared MCP tool-surface contract helpers.

This module owns the canonical Ralph MCP tool naming contract so startup,
runtime, prompts, and provider integrations all derive from the same rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME, RalphToolName, claude_tool_name_prefix
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.mcp.upstream.registry import UpstreamRegistry


def claude_alias_for_tool_name(tool_name: str) -> str | None:
    """Return the strict-MCP alias for a canonical Ralph tool name."""
    try:
        return str(RalphToolName(tool_name).as_claude_alias(server_name=RALPH_MCP_SERVER_NAME))
    except ValueError:
        return None


def canonical_tool_name(tool_name: str) -> str:
    """Collapse a raw or aliased tool name to its canonical raw Ralph name."""
    prefix = claude_tool_name_prefix(server_name=RALPH_MCP_SERVER_NAME)
    if tool_name.startswith(prefix):
        raw_name = tool_name[len(prefix) :]
        try:
            return str(RalphToolName(raw_name))
        except ValueError:
            return tool_name
    return tool_name


def canonicalize_tool_names(tool_names: Iterable[str]) -> tuple[str, ...]:
    """Return deduped canonical raw Ralph tool names in input order."""
    canonical: list[str] = []
    seen: set[str] = set()
    for tool_name in tool_names:
        raw_name = canonical_tool_name(tool_name)
        if raw_name in seen:
            continue
        seen.add(raw_name)
        canonical.append(raw_name)
    return tuple(canonical)


def expand_tool_names_with_aliases(tool_names: Iterable[str]) -> list[str]:
    """Return each canonical tool name plus its strict-MCP alias, deduped."""
    expanded: list[str] = []
    for tool_name in canonicalize_tool_names(tool_names):
        if tool_name not in expanded:
            expanded.append(tool_name)
        alias = claude_alias_for_tool_name(tool_name)
        if alias is not None and alias not in expanded:
            expanded.append(alias)
    return expanded


def visible_owned_tool_names(
    session: object,
    workspace: object,
    *,
    upstream_registry: UpstreamRegistry | None = None,
    include_aliases: bool = True,
) -> list[str]:
    """Return the live visible Ralph-owned tool surface for a session."""
    registry = build_ralph_tool_registry(
        session, workspace, upstream_registry=upstream_registry, mcp_config=None
    )
    tool_names = [definition.name for definition in registry.list_definitions()]
    if not include_aliases:
        return list(canonicalize_tool_names(tool_names))
    return expand_tool_names_with_aliases(tool_names)


def visible_tool_names_for_capabilities(capability_ids: Iterable[str], *, drain: str) -> list[str]:
    """Project capabilities onto the real runtime registry tool surface."""
    session = AgentSession(
        session_id=f"prompt-{drain}",
        run_id=f"prompt-{drain}",
        drain=drain,
        capabilities=set(capability_ids),
    )
    return visible_owned_tool_names(session, MemoryWorkspace(), include_aliases=False)


__all__ = [
    "canonical_tool_name",
    "canonicalize_tool_names",
    "claude_alias_for_tool_name",
    "expand_tool_names_with_aliases",
    "visible_owned_tool_names",
    "visible_tool_names_for_capabilities",
]
