from __future__ import annotations

from typing import cast

from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata


def test_tool_bridge_repairs_schema_declared_json_arguments() -> None:
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
                description="captures structured params",
                input_schema={
                    "type": "object",
                    "properties": {
                        "payload": {"type": "object"},
                        "items": {"type": "array"},
                        "text": {"type": "string"},
                    },
                },
            ),
            required_capability="test",
        ),
        handler,
    )

    bridge.dispatch(
        "structured_tool",
        {
            "payload": '{"targets": "[{\\"path\\": \\"x.py\\", \\"action\\": \\"modify\\"}]"}',
            "items": '["a", "b"]',
            "text": '{"must_remain":"a string because schema says string"}',
        },
    )

    assert seen["payload"] == {"targets": [{"path": "x.py", "action": "modify"}]}
    assert seen["items"] == ["a", "b"]
    assert seen["text"] == '{"must_remain":"a string because schema says string"}'


def test_tool_bridge_repairs_additional_json_like_object_fields() -> None:
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
                name="open_object_tool",
                description="captures open object params",
                input_schema={"type": "object", "additionalProperties": True},
            ),
            required_capability="test",
        ),
        handler,
    )

    bridge.dispatch(
        "open_object_tool",
        {"metadata": '{"scope_items": "[{\\"text\\": \\"a\\"}]"}', "title": "plain"},
    )

    metadata = cast("dict[str, object]", seen["metadata"])
    assert metadata == {"scope_items": [{"text": "a"}]}
    assert seen["title"] == "plain"


def test_tool_bridge_repairs_container_fields_declared_with_refs_and_combinators() -> None:
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
                name="schema_variants_tool",
                description="captures structured params",
                input_schema={
                    "type": "object",
                    "$defs": {
                        "ObjectPayload": {"type": "object"},
                        "ArrayPayload": {"type": "array"},
                    },
                    "properties": {
                        "type_array": {"type": ["string", "array"]},
                        "ref_payload": {"$ref": "#/$defs/ObjectPayload"},
                        "all_of_payload": {"allOf": [{"$ref": "#/$defs/ArrayPayload"}]},
                    },
                },
            ),
            required_capability="test",
        ),
        handler,
    )

    bridge.dispatch(
        "schema_variants_tool",
        {
            "type_array": '["a", "b"]',
            "ref_payload": '{"depends_on": "[1, 2]"}',
            "all_of_payload": (
                '[{"expected_evidence": '
                '"[{\\"kind\\": \\"file\\", \\"ref\\": \\"src/foo.py\\"}]"}]'
            ),
        },
    )

    assert seen["type_array"] == ["a", "b"]
    assert seen["ref_payload"] == {"depends_on": [1, 2]}
    assert seen["all_of_payload"] == [
        {"expected_evidence": [{"kind": "file", "ref": "src/foo.py"}]}
    ]
