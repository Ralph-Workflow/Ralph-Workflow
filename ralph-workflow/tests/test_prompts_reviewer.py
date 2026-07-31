from __future__ import annotations

from ralph.prompts.reviewer import render_review_prompt
from ralph.prompts.template_registry import TemplateRegistry


def test_review_prompt_includes_instructions_and_plan() -> None:
    prompt = render_review_prompt("Implementation plan", "Diff summary")

    assert "Review mode:" in prompt
    assert "Implementation plan" in prompt
    assert "Diff summary" in prompt
    assert "leave code and commits unchanged." in prompt


def test_review_prompt_uses_custom_template_when_available() -> None:
    registry = TemplateRegistry()
    registry.register_template("review", "Custom review: {{ PLAN }} | {{ CHANGES }}")

    prompt = render_review_prompt("Plan content", "Changes content", template_registry=registry)

    assert prompt == "Custom review: Plan content | Changes content"


def test_review_prompt_replaces_empty_plan_or_changes_with_placeholders() -> None:
    prompt = render_review_prompt("", "")

    assert "(no plan available)" in prompt
    assert "(no diff available)" in prompt


def test_review_prompt_fans_out_independent_checks_when_supported() -> None:
    prompt = render_review_prompt("Implementation plan", "Diff summary")

    assert prompt.startswith(
        "Judge the implementation against the plan with fresh evidence and report material findings."
    )
    assert "Review mode: analyze the implementation and report findings; leave code and commits unchanged." in prompt
    assert "subagent" in prompt.lower()
    assert "parallel" in prompt.lower()
    assert "main session" in prompt.lower()
    assert "sequentially" in prompt.lower()
    assert prompt.index("## Fresh review evidence") < prompt.rindex("## Submit")
    assert '`declare_complete(summary="issues")`' in prompt


def test_clean_review_prompt_requires_structured_evidence_for_every_dimension() -> None:
    prompt = render_review_prompt("Implementation plan", "Diff summary")

    assert "`## Review Evidence`" in prompt
    assert "one item per applicable review dimension" in prompt
    assert "one item per plan requirement and acceptance criterion" in prompt
    assert "When `status` is `no_issues`, `## Review Evidence` must be non-empty" in prompt
    for dimension in (
        "Plan compliance",
        "Security",
        "Correctness",
        "Performance",
        "Maintainability",
        "Test coverage",
    ):
        assert f"{dimension} |" in prompt
