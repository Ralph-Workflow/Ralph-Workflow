"""On-the-wire contracts for plan markdown tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools.bridge._specs_artifacts import artifact_specs
from tests._support.typed_accessors import (
    must_mapping,
    must_str_list,
)

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._tool_definition import ToolDefinition


def _specs() -> dict[str, ToolDefinition]:
    return {spec.metadata.definition.name: spec.metadata.definition for spec in artifact_specs()}


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
        typed_properties = must_mapping(properties)
        required = schema["required"]
        assert isinstance(required, list)
        assert all(isinstance(item, str) for item in required)
        typed_required = must_str_list(required)
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


def test_plan_edit_tool_is_not_exposed() -> None:
    """Plan edits now go through the standard stage/replace_all/finalize flow."""
    specs = _specs()
    assert "ralph_edit_md_plan_step" not in specs
    assert (
        "replace_all" in specs["ralph_stage_md_artifact"].input_schema["properties"]["mode"]["enum"]
    )
