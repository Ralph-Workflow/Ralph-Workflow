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
                '[{"expected_evidence": "[{\\"kind\\": \\"file\\", \\"ref\\": \\"src/foo.py\\"}]"}]'
            ),
        },
    )

    assert seen["type_array"] == ["a", "b"]
    assert seen["ref_payload"] == {"depends_on": [1, 2]}
    assert seen["all_of_payload"] == [
        {"expected_evidence": [{"kind": "file", "ref": "src/foo.py"}]}
    ]


def test_tool_bridge_repairs_item_wrappers_for_known_list_fields() -> None:
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
                name="wrapped_plan_tool",
                description="captures wrapped plan params",
                input_schema={
                    "type": "object",
                    "properties": {
                        "payload": {"type": "object"},
                    },
                },
            ),
            required_capability="test",
        ),
        handler,
    )

    bridge.dispatch(
        "wrapped_plan_tool",
        {
            "payload": {
                "scope_items": {"item": [{"text": "a"}, {"text": "b"}, {"text": "c"}]},
                "skills": {"item": ["submit-plan-artifact", "test-driven-development"]},
                "mcps": {"item": "context7"},
                "acceptance_criteria": {
                    "criteria": {
                        "item": [
                            {
                                "id": "AC-01",
                                "description": "A concrete acceptance criterion.",
                                "satisfied_by_steps": {"item": 1},
                            }
                        ]
                    }
                },
            }
        },
    )

    payload = cast("dict[str, object]", seen["payload"])
    assert payload["scope_items"] == [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    assert payload["skills"] == ["submit-plan-artifact", "test-driven-development"]
    assert payload["mcps"] == ["context7"]
    acceptance = cast("dict[str, object]", payload["acceptance_criteria"])
    assert acceptance["criteria"] == [
        {
            "id": "AC-01",
            "description": "A concrete acceptance criterion.",
            "satisfied_by_steps": [1],
        }
    ]


def test_tool_bridge_repairs_item_wrappers_for_schema_declared_arrays() -> None:
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
                name="array_schema_tool",
                description="captures schema-declared arrays",
                input_schema={
                    "type": "object",
                    "properties": {
                        "items": {"type": "array"},
                        "tags": {"type": ["string", "array"]},
                    },
                },
            ),
            required_capability="test",
        ),
        handler,
    )

    bridge.dispatch(
        "array_schema_tool",
        {
            "items": {"item": ["one", "two"]},
            "tags": {"item": "fast-path"},
        },
    )

    assert seen["items"] == ["one", "two"]
    assert seen["tags"] == ["fast-path"]


def test_tool_bridge_repairs_repeated_item_wrappers_for_list_fields() -> None:
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
                name="repeated_wrapped_plan_tool",
                description="captures repeatedly wrapped params",
                input_schema={
                    "type": "object",
                    "properties": {
                        "payload": {"type": "object"},
                        "items": {"type": "array"},
                    },
                },
            ),
            required_capability="test",
        ),
        handler,
    )

    bridge.dispatch(
        "repeated_wrapped_plan_tool",
        {
            "items": {"item": {"item": ["a", "b"]}},
            "payload": {
                "skills": {"item": {"item": ["submit-plan-artifact"]}},
                "mcps": {"item": {"item": "context7"}},
                "acceptance_criteria": {
                    "criteria": {
                        "item": {
                            "item": [
                                {
                                    "id": "AC-01",
                                    "description": "A concrete acceptance criterion.",
                                    "satisfied_by_steps": {"item": {"item": 1}},
                                }
                            ]
                        }
                    }
                },
            },
        },
    )

    payload = cast("dict[str, object]", seen["payload"])
    assert seen["items"] == ["a", "b"]
    assert payload["skills"] == ["submit-plan-artifact"]
    assert payload["mcps"] == ["context7"]
    acceptance = cast("dict[str, object]", payload["acceptance_criteria"])
    assert acceptance["criteria"] == [
        {
            "id": "AC-01",
            "description": "A concrete acceptance criterion.",
            "satisfied_by_steps": [1],
        }
    ]
