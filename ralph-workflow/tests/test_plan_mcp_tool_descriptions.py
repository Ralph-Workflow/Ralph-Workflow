"""On-the-wire contracts for plan markdown tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.bridge._specs_artifacts import artifact_specs

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._tool_definition import ToolDefinition


def _specs() -> dict[str, ToolDefinition]:
    return {
        spec.metadata.definition.name: spec.metadata.definition
        for spec in artifact_specs()
    }


def test_plan_markdown_tools_expose_string_document_schemas() -> None:
    specs = _specs()

    for name in (
        "ralph_submit_md_artifact",
        "ralph_verify_md_artifact",
        "ralph_stage_md_artifact",
    ):
        definition = specs[name]
        schema = definition.input_schema
        properties = schema["properties"]
        assert isinstance(properties, dict)
        typed_properties = cast("dict[str, object]", properties)
        required = schema["required"]
        assert isinstance(required, list)
        assert all(isinstance(item, str) for item in required)
        typed_required = cast("list[str]", required)
        assert typed_properties["artifact_type"] == {"type": "string"}
        assert typed_properties["content"] == {"type": "string"}
        assert {"artifact_type", "content"} <= set(typed_required)


def test_staging_descriptions_cover_resume_repair_and_atomic_finalization() -> None:
    specs = _specs()
    stage = specs["ralph_stage_md_artifact"]
    get = specs["ralph_get_md_draft"]
    finalize = specs["ralph_finalize_md_artifact"]

    assert "persisted draft" in stage.description
    assert "non-gating diagnostics" in stage.description
    assert "resume after interruption" in get.description
    assert "submission gate" in finalize.description
    assert "kept for repair" in finalize.description


def test_plan_edit_schema_uses_stable_ids_and_markdown_replacements() -> None:
    definition = _specs()["ralph_edit_md_plan_step"]
    schema = definition.input_schema
    properties = schema["properties"]
    assert isinstance(properties, dict)
    typed_properties = cast("dict[str, object]", properties)
    action = typed_properties["action"]
    assert isinstance(action, dict)
    typed_action = cast("dict[str, object]", action)

    assert typed_action["enum"] == ["insert", "replace", "remove", "move"]
    assert typed_properties["replacement"] == {"type": "string"}
    assert schema["required"] == ["content", "action", "step_id"]
    assert "stable S-id" in definition.description
    assert "markdown step block" in definition.description
    assert "never JSON" in definition.description
    assert "Depends on:" in definition.description
    assert "Satisfied by:" in definition.description
