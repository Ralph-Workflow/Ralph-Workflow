"""MCP bridge.

Bridges Ralph's phase system with MCP (Model Context Protocol) clients.
Exposes tools for agent interactions, artifact submission, and state queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.mcp.artifacts import (
    DEFAULT_ARTIFACT_PERSISTENCE,
    ArtifactExistsError,
    ArtifactNotFoundError,
    ArtifactPersistence,
    ArtifactSubmitOptions,
    get_artifact,
    list_artifacts,
    submit_artifact,
)
from ralph.mcp.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.transport import MCPMessage, MCPTransport, StdioTransport

if TYPE_CHECKING:
    from collections.abc import Callable

    class _ToolHandler(Protocol):
        """Protocol for MCP tool handler callables."""

        def __call__(self, *args: object, **kwargs: object) -> dict[str, object]: ...

    class _MethodDispatcher(Protocol):
        """Protocol for MCP method dispatcher callables."""

        def __call__(self, message: MCPMessage, /) -> MCPMessage: ...


class BridgeError(Exception):
    """Raised when bridge operations fail."""

    pass


@dataclass(frozen=True)
class BridgeArtifactDeps:
    backend: FileBackend = DEFAULT_FILE_BACKEND
    now_iso: Callable[[], str] = DEFAULT_ARTIFACT_PERSISTENCE.now_iso

    @property
    def persistence(self) -> ArtifactPersistence:
        return ArtifactPersistence(backend=self.backend, now_iso=self.now_iso)


DEFAULT_BRIDGE_ARTIFACT_DEPS = BridgeArtifactDeps()


@dataclass
class BridgeConfig:
    """Configuration for MCP bridge.

    Attributes:
        artifact_dir: Directory for storing artifacts.
        transport: Transport instance (stdio by default).
        workspace_root: Root of the workspace.
    """

    artifact_dir: Path = Path(".agent/artifacts")
    workspace_root: Path = Path()
    transport: MCPTransport | None = None
    artifact_deps: BridgeArtifactDeps = DEFAULT_BRIDGE_ARTIFACT_DEPS


@dataclass
class MCPTool:
    """Represents an MCP tool.

    Attributes:
        name: Tool name.
        description: Tool description.
        input_schema: JSON schema for tool input.
        handler: Callable that handles tool invocations.
    """

    name: str
    description: str
    input_schema: dict[str, object]
    handler: _ToolHandler


class MCPBridge:
    """MCP bridge for Ralph.

    Bridges the phase system with MCP by exposing tools to agents,
    managing artifact lifecycle, and handling MCP protocol messages.
    """

    def __init__(self, config: BridgeConfig) -> None:
        """Initialize MCP bridge.

        Args:
            config: Bridge configuration.
        """
        self._config = config
        self._tools: dict[str, MCPTool] = {}
        self._transport = config.transport or StdioTransport(["echo", "noop"])
        self._running = False

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, object],
        handler: _ToolHandler,
    ) -> None:
        """Register an MCP tool.

        Args:
            name: Tool name.
            description: Tool description.
            input_schema: JSON schema for input validation.
            handler: Function to call when tool is invoked.
        """
        tool = MCPTool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )
        self._tools[name] = tool
        logger.debug("Registered MCP tool: {}", name)

    def tool_called(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        """Handle a tool call from an MCP client.

        Args:
            name: Tool name.
            arguments: Tool arguments.

        Returns:
            Tool result as a dictionary.

        Raises:
            BridgeError: If tool is not found or execution fails.
        """
        tool = self._tools.get(name)
        if not tool:
            raise BridgeError(f"Tool '{name}' not found")

        try:
            logger.debug("Executing tool: {} with args: {}", name, arguments)
            result = tool.handler(**arguments)
            return {"success": True, "result": result}
        except Exception as exc:
            logger.error("Tool '{}' failed: {}", name, exc)
            return {"success": False, "error": str(exc)}

    def submit_artifact_mcp(
        self,
        name: str,
        artifact_type: str,
        content: dict[str, object],
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Submit an artifact via MCP.

        Args:
            name: Artifact name.
            artifact_type: Type of artifact.
            content: Artifact content.
            metadata: Optional metadata.

        Returns:
            Artifact submission result.
        """
        try:
            artifact = submit_artifact(
                self._config.artifact_dir,
                name,
                artifact_type,
                content,
                ArtifactSubmitOptions(
                    metadata=metadata,
                    persistence=self._config.artifact_deps.persistence,
                ),
            )
            return {"success": True, "artifact": artifact.to_dict()}
        except ArtifactExistsError as exc:
            return {"success": False, "error": str(exc)}

    def get_artifact_mcp(self, name: str) -> dict[str, object]:
        """Get an artifact via MCP.

        Args:
            name: Artifact name.

        Returns:
            Artifact data.
        """
        try:
            artifact = get_artifact(
                self._config.artifact_dir,
                name,
                backend=self._config.artifact_deps.backend,
            )
            return {"success": True, "artifact": artifact.to_dict()}
        except ArtifactNotFoundError as exc:
            return {"success": False, "error": str(exc)}

    def list_artifacts_mcp(self) -> dict[str, object]:
        """List all artifacts via MCP.

        Returns:
            List of artifacts.
        """
        artifacts = list_artifacts(
            self._config.artifact_dir,
            backend=self._config.artifact_deps.backend,
        )
        return {
            "success": True,
            "artifacts": [a.to_dict() for a in artifacts],
        }

    async def handle_message(self, message: MCPMessage) -> MCPMessage | None:
        """Handle an incoming MCP message.

        Args:
            message: The MCP message to process.

        Returns:
            Optional response message.
        """
        handler = self._method_dispatchers.get(message.method)
        if handler is not None:
            return handler(message)

        logger.warning("Unknown MCP method: {}", message.method)
        return MCPMessage(
            method=message.method,
            error={"code": -32601, "message": f"Method not found: {message.method}"},
            msg_id=message.msg_id,
        )

    def _dispatch_tools_list(self, message: MCPMessage) -> MCPMessage:
        """Handle tools/list method."""
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in self._tools.values()
        ]
        return MCPMessage(
            method="tools/list",
            params={"tools": tools},
            msg_id=message.msg_id,
        )

    def _dispatch_tools_call(self, message: MCPMessage) -> MCPMessage:
        """Handle tools/call method."""
        if not message.params:
            return MCPMessage(
                method="tools/call",
                error={"code": -32600, "message": "Invalid request"},
                msg_id=message.msg_id,
            )

        tool_name = cast("str", message.params.get("name", "")) or ""
        arguments = cast("dict[str, object]", message.params.get("arguments", {}))
        result = self.tool_called(tool_name, arguments)
        return MCPMessage(
            method="tools/call",
            params={"content": [result]},
            msg_id=message.msg_id,
        )

    def _dispatch_artifacts_submit(self, message: MCPMessage) -> MCPMessage:
        """Handle artifacts/submit method."""
        if not message.params:
            return MCPMessage(
                method="artifacts/submit",
                error={"code": -32600, "message": "Invalid request"},
                msg_id=message.msg_id,
            )

        result = self.submit_artifact_mcp(
            name=cast("str", message.params.get("name", "")),
            artifact_type=cast("str", message.params.get("type", "unknown")),
            content=cast("dict[str, object]", message.params.get("content", {})),
            metadata=cast("dict[str, object] | None", message.params.get("metadata")),
        )
        return MCPMessage(
            method="artifacts/submit",
            params=result,
            msg_id=message.msg_id,
        )

    def _dispatch_artifacts_get(self, message: MCPMessage) -> MCPMessage:
        """Handle artifacts/get method."""
        name = cast("str", message.params.get("name", "")) if message.params else ""
        result = self.get_artifact_mcp(name)
        return MCPMessage(
            method="artifacts/get",
            params=result,
            msg_id=message.msg_id,
        )

    def _dispatch_artifacts_list(self, message: MCPMessage) -> MCPMessage:
        """Handle artifacts/list method."""
        result = self.list_artifacts_mcp()
        return MCPMessage(
            method="artifacts/list",
            params=result,
            msg_id=message.msg_id,
        )

    @property
    def _method_dispatchers(self) -> dict[str, _MethodDispatcher]:
        """Return method dispatchers mapping."""
        return {
            "tools/list": self._dispatch_tools_list,
            "tools/call": self._dispatch_tools_call,
            "artifacts/submit": self._dispatch_artifacts_submit,
            "artifacts/get": self._dispatch_artifacts_get,
            "artifacts/list": self._dispatch_artifacts_list,
        }

    def start(self) -> None:
        """Start the MCP bridge."""
        if isinstance(self._transport, StdioTransport):
            self._transport.start()
        self._running = True
        logger.info("MCP bridge started")

    async def run(self) -> None:
        """Run the bridge message loop."""
        self.start()
        async for message in self._transport.recv():
            response = await self.handle_message(message)
            if response:
                await self._transport.send(response)

    async def close(self) -> None:
        """Close the bridge and transport."""
        self._running = False
        await self._transport.close()
        logger.info("MCP bridge closed")
