"""The tool bridge preserves arguments instead of repairing JSON-shaped strings."""

from __future__ import annotations

from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata


def test_tool_bridge_forwards_arguments_without_json_compensation() -> None:
    """Removed JSON repair must not silently reinterpret markdown-tool input."""
    seen: dict[str, object] = {}

    def handler(
        host_session: object | None,
        workspace: object | None,
        params: dict[str, object],
    ) -> dict[str, object]:
        del host_session, workspace
        seen.update(params)
        return {"ok": True}

    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="structured_tool",
                description="captures params",
                input_schema={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                },
            ),
            required_capability="test",
        ),
        handler,
    )
    content = '{"looks":"like JSON but is artifact text"}'
    metadata = {"item": {"item": ["kept", "verbatim"]}}

    bridge.dispatch("structured_tool", {"content": content, "metadata": metadata})

    assert seen == {"content": content, "metadata": metadata}
