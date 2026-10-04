"""Planning prompts teach substantive, optional parallel conventions."""

from __future__ import annotations

from ralph.prompts.template_context import TemplateContext


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_variants_share_planning_guidance() -> None:
    for name in ("planning.jinja", "planning_fallback.jinja", "planning_edit.jinja", "planning_edit_fallback.jinja"):
        source = _source(name)
        assert "shared/_planning_thinking.j2" in source
        assert "shared/_planning_submission_mechanics.j2" in source


def test_thinking_makes_parallel_the_default_without_a_shape_rule() -> None:
    source = _source("shared/_planning_thinking.jinja")
    for phase in ("Orient", "Characterize", "Change", "Partition", "Verify"):
        assert phase in source
    assert "parallel work is the default" in source
    assert "real coupling" in source


def test_submission_guidance_describes_only_sanity_boundary() -> None:
    source = _source("shared/_planning_submission_mechanics.j2")
    assert "recommended shape" in source
    assert "not a submission format rule" in source
    assert "readable, non-empty" in source
    for forbidden in ("max_work_units", "Validation Overrides", "schema_version", "cycles"):
        assert forbidden not in source


def test_plan_format_doc_teaches_optional_ownership() -> None:
    from ralph.mcp.artifacts.format_docs import load_bundled_format_doc

    source = load_bundled_format_doc("plan")
    assert source is not None
    for text in ("## Work Units", "Paths:", "Directories:", "shared contracts", "integration"):
        assert text in source
