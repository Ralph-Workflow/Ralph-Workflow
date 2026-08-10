"""Planning templates teach one compact executor-ready contract."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_planning_variants_share_thinking_and_submission_partials() -> None:
    for name in (
        "planning.jinja",
        "planning_fallback.jinja",
        "planning_edit.jinja",
        "planning_edit_fallback.jinja",
    ):
        source = _source(name)
        assert "shared/_planning_thinking.j2" in source
        assert "shared/_planning_submission_mechanics.j2" in source


def test_thinking_partial_uses_evidence_and_the_four_work_phases() -> None:
    source = _source("shared/_planning_thinking.jinja")

    for phase in ("Orient", "Characterize", "Change", "Verify"):
        assert phase in source
    assert "Inspect the repository before naming paths or commands" in source
    assert "discovery step for an honest unknown" in source


def test_submission_partial_names_the_mandatory_contract() -> None:
    source = _source("shared/_planning_submission_mechanics.j2")

    assert ".agent/artifact-formats/plan.md" not in source
    assert "stable `### [S-n] Title` steps" in source
    assert "Validation Overrides" in source
    assert "schema_version" in source
