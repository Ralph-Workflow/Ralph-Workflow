"""ToolDefinition dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject


@dataclass(frozen=True)
class ToolDefinition:
    """Immutable description of one tool that the MCP bridge exposes to agents.

    A :class:`ToolDefinition` is the wire-format description a Ralph MCP
    bridge returns for every registered tool when an agent asks the server
    for its capabilities (the MCP ``tools/list`` response). It is also the
    shape Ralph uses internally when it logs or diffs registered tools, so
    the fields here match the public MCP contract rather than the
    bridge's internal handler-bound representation.

    The dataclass is frozen to guarantee that a tool's advertised surface
    cannot drift after the bridge registers it; mutating an instance
    raises :class:`dataclasses.FrozenInstanceError`. New variants must be
    created via :func:`dataclasses.replace` instead.

    Attributes:
        name: Stable, dot-free tool name surfaced to agents
            (e.g. ``"read_file"``, ``"submit_artifact"``). The name is the
            ``tool`` field an agent sends back when invoking the tool, so
            it must be unique within a single bridge and must remain stable
            across releases (rename = breaking change).
        description: Human-readable one-paragraph description of what the
            tool does, shown to agents when they enumerate the server's
            capabilities. Plain text; embedded Markdown is rendered
            literally. Author for clarity rather than brevity.
        input_schema: JSON Schema (draft 2020-12) describing the tool's
            accepted arguments. Must round-trip through
            :func:`ralph.mcp.tools.bridge._types.JsonObject` and validate
            the bridge's pre-dispatch payload. Schemas with ``additionalProperties: false``
            and explicit ``required`` arrays are preferred because they
            make agent-prompted errors unambiguous.

    Invariants:
        - ``name`` is treated as part of the public API; renaming breaks
          any agent prompt that calls the tool by name.
        - ``input_schema`` is consumed verbatim by agents and by the
          bridge's argument validator, so changes to it must keep the
          existing accepted shape backward-compatible or be paired with a
          deprecation note.
    """

    name: str
    description: str
    input_schema: JsonObject
