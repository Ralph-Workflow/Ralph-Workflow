"""Planning prompts lead with repository-grounded thinking."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_planning_variants_share_the_thinking_contract() -> None:
    for name in ("planning.jinja", "planning_fallback.jinja", "planning_edit.jinja", "planning_edit_fallback.jinja"):
        source = _source(name)
        assert "shared/_planning_thinking.j2" in source
        assert "shared/_planning_submission_mechanics.j2" in source


def test_shared_thinking_contract_requires_the_four_work_phases() -> None:
    source = _source("shared/_planning_thinking.jinja")
    for phase in ("Orient", "Characterize", "Change", "Verify"):
        assert phase in source
    assert "even for easy-looking work" in source


def test_shared_thinking_contract_prefers_evidence_to_guesses() -> None:
    source = _source("shared/_planning_thinking.jinja")
    assert "Ground claims" in source
    assert "turn unknowns into a discovery step" in source


def test_planning_prompt_frames_the_request_before_mechanics_and_places_payload_last() -> None:
    source = _source("planning.jinja")

    assert source.index("USER REQUEST: produce") < source.index("PLANNING MODE")
    assert source.index("PLANNING MODE") < source.index("shared/_planning_thinking.j2")
    assert source.index("shared/_planning_thinking.j2") < source.index(
        "shared/_planning_submission_mechanics.j2"
    ) < source.rindex("render_payload_section('PROMPT'")


def test_planning_variants_keep_thinking_before_submission_and_payload() -> None:
    payload_markers = {
        "planning_fallback.jinja": "REQUEST (`{{PROMPT_PATH}}`):",
        "planning_edit.jinja": "ORIGINAL REQUEST:",
        "planning_edit_fallback.jinja": "REQUEST (`{{PROMPT_PATH}}`):",
    }
    for name, payload in payload_markers.items():
        source = _source(name)
        assert source.index("shared/_planning_thinking.j2") < source.index(
            "shared/_planning_submission_mechanics.j2"
        ) < source.index(payload)


def test_submission_mechanics_keeps_plan001_and_standard_artifact_flow() -> None:
    source = _source("shared/_planning_submission_mechanics.j2")
    assert "`PLAN001` is the only blocking plan diagnostic" in source
    assert "Warnings and info are advice" in source
    assert "Validation Overrides" in source
    for variable in (
        "SUBMIT_MD_ARTIFACT_TOOL_REFERENCE",
        "EDIT_MD_ARTIFACT_TOOL_REFERENCE",
        "STAGE_MD_ARTIFACT_TOOL_REFERENCE",
        "GET_MD_DRAFT_TOOL_REFERENCE",
        "FINALIZE_MD_ARTIFACT_TOOL_REFERENCE",
    ):
        assert variable in source


def test_planning_analysis_is_shorter_than_the_previous_property_rubric() -> None:
    source = _source("planning_analysis.jinja")
    assert "do not grade document shape" in source
    assert "Each finding must say the observation" in source
    assert "completed` with no findings is the normal result" in source
    assert "## PLAN QUALITY RUBRIC" not in source
