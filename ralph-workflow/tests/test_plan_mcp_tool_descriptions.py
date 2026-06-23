"""Regression test for the on-the-wire planning MCP tool descriptions.

The 4 step-mutation tool descriptions (INSERT, REPLACE, REMOVE, MOVE) must
explicitly state the auto-reindex, depends_on-rewrite, and AC-remap behavior
on the wire so a cheaper model can rely on the implicit intentions from
`tools/list` alone. The SUBMIT_PLAN_SECTION description must reference the
4 MB total byte cap and `PlanSizeLimits.DEFAULT`. The FINALIZE_PLAN
description must reference the depends_on cycle detector.
"""

from __future__ import annotations

from typing import cast

from ralph.mcp.tools.bridge._specs_artifacts import artifact_specs

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
        s.metadata.definition.name: s.metadata.definition.input_schema
        for s in artifact_specs()
    }


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
    assert "skills_mcp" not in desc
    assert "verification_strategy" not in desc


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
    assert move_properties["to_index"] == {
        "anyOf": [{"type": "integer"}, {"type": "string"}]
    }
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
