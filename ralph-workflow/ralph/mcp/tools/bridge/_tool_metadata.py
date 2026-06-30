"""ToolMetadata dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._tool_definition import ToolDefinition


@dataclass(frozen=True)
class ToolMetadata:
    """Ralph-side metadata paired with a :class:`ToolDefinition` for registration.

    Where :class:`ToolDefinition` is the public agent-facing shape, a
    :class:`ToolMetadata` carries the Ralph-only annotations the bridge and
    authorization layer need to *use* the tool safely:

      - the capability an agent must hold before the dispatcher will
        forward a call,
      - whether the tool mutates workspace state (driving access-mode
        enforcement), and
      - whether the tool accepts multimodal (image or audio) payloads.

    The dataclass is frozen and stored in the bridge's tool registry; every
    invocation consults ``required_capability`` against the agent's
    declared capabilities to decide whether to accept or reject the call.

    Attributes:
        definition: The agent-facing :class:`ToolDefinition` advertised by
            ``tools/list``. Carries the public name, description, and JSON
            schema; :class:`ToolMetadata` adds the Ralph-side annotations.
        required_capability: Name of the capability a calling agent must
            hold (e.g. ``"write_files"``, ``"submit_artifact"``). The
            bridge uses this to gate calls: an agent without the
            capability sees ``PermissionDenied`` instead of a dispatch.
            The default-deny invariant in :mod:`ralph.mcp` documents the
            policy that every Ralph-managed tool must declare one.
        is_mutating: ``True`` when the tool can change workspace or
            artifact-store state, ``False`` for strictly read-only tools.
            ``None`` is treated as conservative (``True``) so an unknown
            tool is assumed to mutate until the registration proves
            otherwise. The bridge uses this flag together with
            :func:`ralph.mcp.protocol.startup.access_mode_for_drain` to
            decide whether a read-only session can still call the tool.
        is_multimodal: ``True`` when the tool accepts binary content
            (images, audio) alongside its text schema. The bridge surfaces
            multimodal tools through a different ``callTool`` content
            shape and tags them so agents can discover them via the
            metadata flag.

    Invariants:
        - ``required_capability`` must be a non-empty capability name
          recognized by :mod:`ralph.mcp.capabilities`; unknown
          capabilities cause the dispatcher to reject every call.
        - The combination of ``is_mutating=False`` and
          ``required_capability`` granting write access is allowed but
          downgrades the runtime access mode on a read-only session.
        - :class:`ToolMetadata` is constructed once, at registry build
          time, and is then immutable for the lifetime of the bridge.
    """

    definition: ToolDefinition
    required_capability: str
    is_mutating: bool | None = None
    is_multimodal: bool = False
