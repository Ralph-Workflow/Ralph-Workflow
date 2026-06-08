"""ToolBridge registry and dispatcher."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.bridge._lazy_tool_handler import LazyToolHandler
from ralph.mcp.tools.bridge._registered_tool import RegisteredTool
from ralph.mcp.tools.bridge._spec_helpers import _is_approved
from ralph.mcp.tools.bridge._tool_bridge_error import ToolBridgeError
from ralph.mcp.tools.bridge._tool_dispatch_error import ToolDispatchError
from ralph.mcp.tools.bridge._tool_registration_error import ToolRegistrationError
from ralph.mcp.tools.capability_denied_error import CapabilityDeniedError
from ralph.mcp.tools.invalid_params_error import InvalidParamsError
from ralph.mcp.tools.tool_content import ToolContent
from ralph.mcp.tools.tool_error import ToolError
from ralph.mcp.tools.tool_result import ToolResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.tools.bridge._registration_handler import RegistrationHandler
    from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
    from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
    from ralph.mcp.tools.bridge._tool_spec import ToolSpec
    from ralph.mcp.tools.bridge._types import JsonObject


class ToolBridge:
    """Registry for MCP tools and dispatcher for tool invocations."""

    def __init__(self, session: object | None = None) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._session = session
        self._client_capabilities: set[str] | None = None

    def set_client_capabilities(self, capabilities: set[str] | None) -> None:
        """Set the client declared capabilities from MCP initialize handshake."""
        self._client_capabilities = capabilities

    def register(self, metadata: ToolMetadata, handler: RegistrationHandler) -> None:
        """Register a tool definition and handler."""
        name = metadata.definition.name
        if name in self._tools:
            raise ToolRegistrationError(f"Tool '{name}' is already registered")
        self._tools[name] = RegisteredTool(metadata=metadata, handler=handler)

    def register_spec(self, spec: ToolSpec, *, session: object, workspace: object) -> None:
        """Register a tool from a complete lazy-loading spec."""
        self.register(
            spec.metadata,
            LazyToolHandler(
                module_name=spec.module_name,
                handler_name=spec.handler_name,
                session=session,
                workspace=workspace,
            ),
        )

    def has_tool(self, name: str) -> bool:
        """Return whether a tool is registered."""
        return name in self._tools

    def get(self, name: str) -> RegisteredTool:
        """Return a registered tool or raise if it does not exist."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolDispatchError(f"Tool '{name}' is not registered") from exc

    def list_metadata(self) -> list[ToolMetadata]:
        """Return tool metadata in registration order."""
        return [
            tool.metadata
            for tool in self._tools.values()
            if self._is_tool_allowed(tool.metadata) and self._is_tool_visible(tool.metadata)
        ]

    def list_definitions(self) -> list[ToolDefinition]:
        """Return public tool definitions in registration order."""
        return [
            tool.metadata.definition
            for tool in self._tools.values()
            if self._is_tool_allowed(tool.metadata) and self._is_tool_visible(tool.metadata)
        ]

    def dispatch(
        self,
        name: str,
        params: JsonObject | None = None,
        *,
        host_session: object | None = None,
        workspace: object | None = None,
    ) -> object:
        """Dispatch a tool invocation to its registered handler."""
        tool = self.get(name)
        session = host_session or self._session
        if not self._is_tool_allowed(tool.metadata, session=session):
            capability = tool.metadata.required_capability
            raise ToolDispatchError(f"Tool '{name}' requires capability '{capability}'")
        tool_params = dict(params or {})
        try:
            return tool.handler(host_session, workspace, tool_params)
        except ToolBridgeError:
            raise
        except (InvalidParamsError, CapabilityDeniedError):
            # Fix-your-call errors: the agent must change the call before it can
            # succeed, so they correctly surface as protocol errors — retrying the
            # identical call is rightly rejected again.
            raise
        except ToolError as exc:
            # Operational tool failure (timeout, output/size limit, spawn/IO/git
            # failure). Returning a retryable -32603 protocol error here is what let
            # an agent re-issue an identical failing call for ~5 hours. Convert to a
            # terminal, non-retryable is_error result so every handler is covered by
            # one guard even if it forgets to convert the failure itself.
            return ToolResult(
                content=[ToolContent.text_content(str(exc))],
                is_error=True,
            )
        except Exception as exc:
            raise ToolDispatchError(f"Tool '{name}' failed: {exc}") from exc

    def _is_tool_visible(self, metadata: ToolMetadata) -> bool:
        """Check if a tool is visible to the client based on multimodal flags."""
        if not metadata.is_multimodal:
            return True

        if self._client_capabilities is None:
            return False

        client_caps = self._client_capabilities
        return (
            "image" in client_caps
            or "media" in client_caps
            or "multimodal" in client_caps
            or "MediaRead" in client_caps
            or "media.read" in client_caps
        )

    def _is_tool_allowed(self, metadata: ToolMetadata, session: object | None = None) -> bool:
        effective_session = session or self._session
        if effective_session is None:
            return True

        checker = cast(
            "Callable[[str], object] | None",
            getattr(effective_session, "check_capability", None),
        )
        if not callable(checker):
            return True

        return _is_approved(checker(metadata.required_capability))
