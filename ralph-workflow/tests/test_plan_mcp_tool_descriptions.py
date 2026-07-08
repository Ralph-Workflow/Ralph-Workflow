"""Regression test for the on-the-wire planning MCP tool descriptions.

The 4 step-mutation tool descriptions (INSERT, REPLACE, REMOVE, MOVE) must
explicitly state the auto-reindex, depends_on-rewrite, and AC-remap behavior
on the wire so a cheaper model can rely on the implicit intentions from
`tools/list` alone. The SUBMIT_PLAN_SECTION description must reference the
4 MB total byte cap and `PlanSizeLimits.DEFAULT`. The FINALIZE_PLAN
description must reference the depends_on cycle detector.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.tools.artifact import (
    handle_submit_plan_section,
    handle_submit_plan_sections,
    prepare_artifact_submission,
)
from ralph.mcp.tools.bridge._spec_helpers import (
    _EXAMPLE_PLAN_SECTION_CONTENT,
    _EXAMPLE_STEPS_CONTENT,
    _SUBMIT_ARTIFACT_DESCRIPTION,
)
from ralph.mcp.tools.bridge._specs_artifacts import artifact_specs
from ralph.mcp.tools.tool_result import ToolResult
from ralph.workspace.fs import FsWorkspace
from tests.test_plan_artifact_submit_plan_sections import planning_session

if TYPE_CHECKING:
    from pathlib import Path

_MUTATION_TOOLS = (
    "ralph_insert_plan_step",
    "ralph_replace_plan_step",
    "ralph_patch_step",
    "ralph_remove_plan_step",
    "ralph_move_plan_step",
)


def _descs() -> dict[str, str]:
    return {s.metadata.definition.name: s.metadata.definition.description for s in artifact_specs()}


def _schemas() -> dict[str, dict[str, object]]:
    return {
        s.metadata.definition.name: s.metadata.definition.input_schema for s in artifact_specs()
    }


def _extract_balanced_json_after(text: str, marker: str) -> dict[str, object]:
    start = text.index(marker) + len(marker)
    while text[start].isspace():
        start += 1
    assert text[start] == "{"
    depth = 0
    in_string = False
    escape = False
    for offset, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                decoded = json.loads(text[start : offset + 1])
                assert isinstance(decoded, dict)
                return cast("dict[str, object]", decoded)
    raise AssertionError(f"could not extract JSON object after {marker!r}")


def test_step_mutation_descriptions_contain_reindex_depends_on_satisfied_by_steps() -> None:
    """Every step-mutation tool description contains 'reindex', 'depends_on', and
    'satisfied_by_steps' so a cheaper agent reading `tools/list` can rely on the
    implicit-intention contract without reading source."""
    descs = _descs()
    for name in _MUTATION_TOOLS:
        desc = descs[name]
        assert "reindex" in desc, f"{name} description missing 'reindex': {desc[:200]}"
        assert "depends_on" in desc, f"{name} description missing 'depends_on': {desc[:200]}"
        assert "satisfied_by_steps" in desc, (
            f"{name} description missing 'satisfied_by_steps': {desc[:200]}"
        )


def test_submit_plan_section_description_references_size_limits() -> None:
    """SUBMIT_PLAN_SECTION description references the 4 MB total byte cap and
    `PlanSizeLimits.DEFAULT` so a cheaper model knows the byte cap and per-list
    caps are checked before Pydantic."""
    descs = _descs()
    desc = descs["ralph_submit_plan_section"]
    assert "4 MB" in desc, f"SUBMIT_PLAN_SECTION description missing '4 MB': {desc[:200]}"
    assert "PlanSizeLimits" in desc, (
        f"SUBMIT_PLAN_SECTION description missing 'PlanSizeLimits': {desc[:200]}"
    )
    assert "depends_on" in desc, (
        f"SUBMIT_PLAN_SECTION description missing 'depends_on': {desc[:200]}"
    )


def test_finalize_plan_description_references_cycle_detector() -> None:
    """FINALIZE_PLAN description references the depends_on cycle detector so a
    cheaper model knows a cyclic graph is rejected with a named entry point."""
    descs = _descs()
    desc = descs["ralph_finalize_plan"]
    assert "cycle" in desc, f"FINALIZE_PLAN description missing 'cycle': {desc[:200]}"
    assert "detected at step" in desc, (
        f"FINALIZE_PLAN description missing 'detected at step': {desc[:200]}"
    )


def test_submit_artifact_description_references_pydantic_and_byte_cap() -> None:
    """SUBMIT_ARTIFACT description references Pydantic validation and the 4 MB
    byte cap so the byte cap and the Pydantic ordering are explicit on the wire."""
    descs = _descs()
    desc = descs["ralph_submit_artifact"]
    assert "Pydantic" in desc, f"SUBMIT_ARTIFACT description missing 'Pydantic': {desc[:200]}"
    assert "4 MB" in desc, f"SUBMIT_ARTIFACT description missing '4 MB': {desc[:200]}"


def test_submit_artifact_description_sends_plans_to_planning_tools() -> None:
    descs = _descs()
    desc = descs["ralph_submit_artifact"]
    assert "Do not use this generic tool for plan artifacts" in desc
    assert "ralph_submit_plan_section" in desc


def test_submit_artifact_description_example_matches_validator() -> None:
    match = re.search(
        r"Example: (?P<example>\{.*?\})\. See",
        _SUBMIT_ARTIFACT_DESCRIPTION,
    )
    assert match is not None
    payload = json.loads(match.group("example"))

    artifact_type, normalized = prepare_artifact_submission(payload)

    assert artifact_type == "commit_message"
    assert normalized["subject"] == "fix(auth): prevent token expiry race"


@pytest.mark.parametrize(
    ("section", "content"),
    [
        ("summary", _EXAMPLE_PLAN_SECTION_CONTENT),
        ("steps", _EXAMPLE_STEPS_CONTENT),
    ],
)
def test_plan_section_description_examples_are_detailed_and_stage_cleanly(
    tmp_path: Path,
    section: str,
    content: str,
) -> None:
    assert "Tweak the config key" not in content
    assert "Detailed executor instructions" not in content
    assert "Concrete outcome" not in content
    assert "expected_evidence" in content or "scope_items" in content

    result = handle_submit_plan_section(
        planning_session(),
        FsWorkspace(tmp_path),
        {"section": section, "content": content, "mode": "replace"},
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    response = json.loads(result.content[0].text)
    assert response["validation_warnings"] == []


def test_plan_section_tool_description_example_round_trips(tmp_path: Path) -> None:
    desc = _descs()["ralph_submit_plan_section"]
    payload = _extract_balanced_json_after(desc, "Example: ")

    result = handle_submit_plan_section(
        planning_session(),
        FsWorkspace(tmp_path),
        payload,
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False, result.content[0].text
    response = json.loads(result.content[0].text)
    assert response["validation_warnings"] == []


def test_plan_sections_tool_description_example_round_trips(tmp_path: Path) -> None:
    desc = _descs()["ralph_submit_plan_sections"]
    payload = _extract_balanced_json_after(desc, "for example ")

    result = handle_submit_plan_sections(
        planning_session(),
        FsWorkspace(tmp_path),
        payload,
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False, result.content[0].text
    response = json.loads(result.content[0].text)
    assert response["validation_warnings"] == []


def test_submit_artifact_schema_accepts_native_json_content() -> None:
    schema = _schemas()["ralph_submit_artifact"]
    properties = schema.get("properties")
    assert isinstance(properties, dict)
    content_schema = properties["content"]
    assert isinstance(content_schema, dict)
    assert content_schema["anyOf"] == [
        {"type": "string"},
        {"type": "object"},
        {"type": "array"},
    ]


def test_discard_plan_draft_description_discourages_warning_retry_loops() -> None:
    descs = _descs()
    desc = descs["ralph_discard_plan_draft"]
    assert "truly starting over" in desc
    assert "unsalvageable" in desc
    assert "Do not use this to clear ordinary validation_warnings" in desc
    assert "ralph_validate_draft" in desc


def test_validate_draft_description_documents_runtime_error_object_shape() -> None:
    descs = _descs()
    desc = descs["ralph_validate_draft"]
    assert '{"valid":false,"finalizable":false,"errors":[{"message":' in desc
    assert '"code":"SUMMARY_MISSING_SCOPE_ITEMS"' in desc
    assert '"repair":"Re-submit section' in desc
    assert 'errors":["summary: required field is missing"]' not in desc


def test_replace_and_patch_descriptions_include_analysis_feedback_proof_fields() -> None:
    descs = _descs()
    replace_desc = descs["ralph_replace_plan_step"]
    patch_desc = descs["ralph_patch_step"]
    for desc in (replace_desc, patch_desc):
        assert "analysis feedback" in desc
        assert "targets" in desc
        assert "expected_evidence" in desc
        assert "depends_on" in desc


def test_step_tools_schema_exposes_flat_argument_style() -> None:
    """tools/list must advertise the flat fields that handlers accept."""
    schemas = _schemas()
    for name in ("ralph_insert_plan_step", "ralph_replace_plan_step", "ralph_patch_step"):
        schema = schemas[name]
        required = set(schema.get("required", []))
        assert "step" not in required, f"{name} schema must not require nested step only"
        properties = schema.get("properties")
        assert isinstance(properties, dict)
        flat_fields = (
            "title",
            "content",
            "step_type",
            "targets",
            "depends_on",
            "expected_evidence",
        )
        for field in flat_fields:
            assert field in properties, f"{name} schema does not advertise flat field {field!r}"
        assert {"required": ["step"]} in schema.get("anyOf", [])


def test_step_tools_schema_exposes_repaired_container_argument_shapes() -> None:
    """tools/list must match handler JSON-container repair for step edits."""
    schemas = _schemas()
    for name in ("ralph_insert_plan_step", "ralph_replace_plan_step", "ralph_patch_step"):
        properties = schemas[name].get("properties")
        assert isinstance(properties, dict)
        assert properties["step"] == {
            "anyOf": [{"type": "object"}, {"type": "string"}],
            "description": "Native step object or a JSON-serialized step object.",
        }
        assert properties["targets"] == {
            "anyOf": [{"type": "array"}, {"type": "object"}, {"type": "string"}]
        }
        assert properties["depends_on"] == {
            "anyOf": [{"type": "array"}, {"type": "integer"}, {"type": "string"}]
        }
        assert properties["satisfies"] == {"anyOf": [{"type": "array"}, {"type": "string"}]}
        assert properties["expected_evidence"] == {
            "anyOf": [{"type": "array"}, {"type": "object"}, {"type": "string"}]
        }


def test_planning_tool_descriptions_advertise_lenient_staging_and_warnings() -> None:
    descs = _descs()
    for name in (
        "ralph_submit_plan_section",
        "ralph_submit_plan_sections",
        "ralph_insert_plan_step",
        "ralph_replace_plan_step",
        "ralph_patch_step",
        "ralph_remove_plan_step",
        "ralph_move_plan_step",
    ):
        desc = descs[name]
        assert "validation_warnings" in desc
        assert "validate_draft" in desc or name.startswith("ralph_submit_plan")

    forbidden_by_tool = {
        "ralph_submit_plan_sections": ("validates ALL", "first failure"),
        "ralph_remove_plan_step": ("Fails fast", "Silently drops"),
    }
    for name, fragments in forbidden_by_tool.items():
        desc = descs[name]
        for fragment in fragments:
            assert fragment not in desc, f"{name} description still says {fragment!r}"


def test_step_index_schemas_accept_numeric_strings_and_edge_indexes() -> None:
    schemas = _schemas()
    insert_index = schemas["ralph_insert_plan_step"]["properties"]["index"]
    assert insert_index == {"anyOf": [{"type": "integer"}, {"type": "string"}]}
    move_properties = schemas["ralph_move_plan_step"]["properties"]
    assert move_properties["to_index"] == {"anyOf": [{"type": "integer"}, {"type": "string"}]}
    assert "minimum" not in cast("dict[str, object]", move_properties["to_index"])


def test_planning_tool_descriptions_do_not_advertise_pseudo_json_call_examples() -> None:
    """Descriptions are the tools/list contract; examples there must not be Python dict syntax."""
    descs = _descs()
    planning_tool_names = [
        name
        for name in descs
        if name.startswith("ralph_")
        and (
            "plan" in name
            or name
            in {
                "ralph_submit_artifact",
                "ralph_patch_step",
            }
        )
    ]
    forbidden_fragments = ("{'", "':", "{section:", "{action:", "{valid:", "source: '")
    for name in planning_tool_names:
        desc = descs[name]
        for fragment in forbidden_fragments:
            assert fragment not in desc, (
                f"{name} description contains pseudo-JSON fragment {fragment!r}: {desc}"
            )


def test_read_only_tool_descriptions_mark_themselves_as_noop_or_draft() -> None:
    """GET_PLAN_DRAFT and DISCARD_PLAN_DRAFT descriptions each contain 'noop' or
    'draft' so a cheaper model can identify them as read-only/noop tools
    without needing reindex keywords."""
    descs = _descs()
    for name in ("ralph_get_plan_draft", "ralph_discard_plan_draft"):
        desc = descs[name]
        assert ("noop" in desc) or ("draft" in desc), (
            f"{name} description missing 'noop' or 'draft': {desc[:200]}"
        )
