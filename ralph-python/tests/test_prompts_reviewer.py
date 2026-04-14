from __future__ import annotations

from ralph.prompts.reviewer import render_review_prompt
from ralph.prompts.template_registry import TemplateRegistry


def test_review_prompt_includes_instructions_and_plan() -> None:
    prompt = render_review_prompt("Implementation plan", "Diff summary")

    assert "REVIEW MODE" in prompt
    assert "Implementation plan" in prompt
    assert "Diff summary" in prompt
    assert "Your only job" in prompt


def test_review_prompt_uses_custom_template_when_available() -> None:
    registry = TemplateRegistry()
    registry.register_template("review", "Custom review: {PLAN} | {CHANGES}")

    prompt = render_review_prompt("Plan content", "Changes content", template_registry=registry)

    assert prompt == "Custom review: Plan content | Changes content"


def test_review_prompt_replaces_empty_plan_or_changes_with_placeholders() -> None:
    prompt = render_review_prompt("", "")

    assert "(no plan available)" in prompt
    assert "(no diff available)" in prompt
