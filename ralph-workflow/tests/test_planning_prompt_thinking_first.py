"""Planning prompts lead with repository-grounded thinking."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_planning_variants_share_the_thinking_contract() -> None:
    for name in ("planning.jinja", "planning_fallback.jinja", "planning_edit.jinja", "planning_edit_fallback.jinja"):
        assert "shared/_planning_thinking.j2" in _source(name)


def test_shared_thinking_contract_requires_the_four_work_phases() -> None:
    source = _source("shared/_planning_thinking.jinja")
    for phase in ("Orient", "Characterize", "Change", "Verify"):
        assert phase in source
    assert "even for easy-looking work" in source


def test_shared_thinking_contract_prefers_evidence_to_guesses() -> None:
    source = _source("shared/_planning_thinking.jinja")
    assert "Ground claims" in source
    assert "turn unknowns into a discovery step" in source


def test_shared_thinking_contract_states_plan001_is_the_only_blocker() -> None:
    source = _source("shared/_planning_thinking.jinja")
    assert "`PLAN001` is the only blocking plan diagnostic" in source
    assert "Warnings and info are advice" in source
    assert "Validation Overrides" in source


def test_shared_thinking_contract_teaches_standard_artifact_revision() -> None:
    source = _source("shared/_planning_thinking.jinja")
    for variable in (
        "SUBMIT_MD_ARTIFACT_TOOL_REFERENCE",
        "EDIT_MD_ARTIFACT_TOOL_REFERENCE",
        "STAGE_MD_ARTIFACT_TOOL_REFERENCE",
        "GET_MD_DRAFT_TOOL_REFERENCE",
        "FINALIZE_MD_ARTIFACT_TOOL_REFERENCE",
    ):
        assert variable in source


def test_primary_prompt_keeps_thinking_before_the_request() -> None:
    source = _source("planning.jinja")
    assert source.index("shared/_planning_thinking.j2") < source.index("USER REQUEST:")


def test_planning_analysis_is_shorter_than_the_previous_property_rubric() -> None:
    source = _source("planning_analysis.jinja")
    assert "do not grade document shape" in source
    assert "Each finding must say the observation" in source
    assert "completed` with no findings is the normal result" in source
    assert "## PLAN QUALITY RUBRIC" not in source
