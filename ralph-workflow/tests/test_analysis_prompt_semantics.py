"""Semantic contracts for the planning, development, and review analysis prompts."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.format_docs import (
    load_bundled_example,
    load_bundled_format_doc,
)
from ralph.prompts.template_context import TemplateContext

_ROLE_LINES = {
    "planning_analysis": (
        "You are the planning analysis reviewer. "
        "Judge executor readiness and submit one decision artifact."
    ),
    "development_analysis": (
        "You are the development analysis reviewer. "
        "Judge the current implementation against the plan and submit one decision artifact."
    ),
    "review_analysis": (
        "You are the review-analysis judge. "
        "Grade the submitted review, not the implementation itself."
    ),
}

_COMPLETED_STATUS_RULES = {
    "planning_analysis": (
        "Choose `status: completed` when the plan is sound across every "
        "applicable, evaluatable dimension."
    ),
    "development_analysis": (
        "Choose `status: completed` when no observable gap exists for every "
        "applicable, evaluatable requirement and checklist dimension."
    ),
    "review_analysis": (
        "Choose `status: completed` when the submitted review is thorough and "
        "correct across every applicable, evaluatable dimension."
    ),
}


def _template(name: str) -> str:
    return TemplateContext.default().registry.get_template(name)


def _normalized(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize(("name", "role_line"), _ROLE_LINES.items())
def test_analysis_prompt_role_is_the_first_nonempty_line(name: str, role_line: str) -> None:
    source = _template(name)

    first_nonempty = next(line for line in source.splitlines() if line.strip())

    assert first_nonempty == role_line


@pytest.mark.parametrize("name", _ROLE_LINES)
def test_analysis_prompt_keeps_decision_and_submission_contract_late(name: str) -> None:
    source = _template(name)

    checklist_index = source.index("## REVIEW CHECKLIST")
    decision_index = source.index("## DECISION ARTIFACT")
    submission_index = source.rindex("render_artifact_submission(")

    assert checklist_index < decision_index < submission_index
    assert source.rstrip().splitlines()[-1].startswith("{{ render_artifact_submission(")


@pytest.mark.parametrize("name", _ROLE_LINES)
def test_analysis_prompt_scores_only_applicable_evaluatable_dimensions(
    name: str,
) -> None:
    normalized = _normalized(_template(name))

    assert "across all applicable, evaluatable dimensions" in normalized
    assert "across all dimensions" not in normalized


@pytest.mark.parametrize(
    ("name", "completed_rule"),
    _COMPLETED_STATUS_RULES.items(),
)
def test_analysis_prompt_completed_status_uses_evaluatable_scope(
    name: str,
    completed_rule: str,
) -> None:
    normalized = _normalized(_template(name))

    assert completed_rule in normalized
    assert "across ALL evaluatable dimensions" not in normalized


def test_planning_analysis_treats_product_criteria_as_the_goal() -> None:
    source = _template("planning_analysis")
    normalized = _normalized(source)

    assert (
        "The PRODUCT CRITERIA define the goal you are judging against; "
        "the PLAN is the artifact you are evaluating."
    ) in normalized
    assert "Judge the plan against the PRODUCT CRITERIA" in normalized
    assert "The PLAN defines the goal you are judging against" not in normalized


def test_planning_analysis_uses_product_criteria_compliance_terminology() -> None:
    source = _template("planning_analysis")

    assert "Product Criteria Compliance" in source
    assert "Prompt Compliance" not in source


@pytest.mark.parametrize("name", _ROLE_LINES)
def test_analysis_prompt_teaches_relational_decision_invariants(name: str) -> None:
    normalized = _normalized(_template(name))

    assert (
        "A completed decision must omit both remediation sections; any known "
        "gap requires a non-completed status."
    ) in normalized
    assert (
        "The two remediation ID sets must match exactly: no missing, extra, "
        "or mismatched gap/fix IDs."
    ) in normalized


def test_review_analysis_uses_the_submitted_review_as_direct_evidence() -> None:
    source = _template("review_analysis")
    normalized = _normalized(source)

    assert (
        "The submitted review artifact is direct evidence of what the review "
        "covered, reported, and classified."
    ) in normalized
    assert "you are NOT re-doing the code review" not in normalized
    assert "The review artifact plus" not in normalized
    assert (
        "The review artifact plus any developer- or reviewer-produced summaries, "
        "narratives, and handoff artifacts are locators and context — never evidence"
    ) not in normalized


def test_review_analysis_links_verification_defects_to_the_submitted_review() -> None:
    normalized = _normalized(_template("review_analysis"))

    assert (
        "the result shows that the submitted review omitted or misstated the verification state"
    ) in normalized
    assert (
        "A review-quality gap exists only when you prove that the submitted "
        "review omitted or misstated"
    ) in normalized
    assert "A gap exists only when you proved it: a command you ran fails" not in normalized


def test_review_analysis_statuses_grade_review_quality_only() -> None:
    source = _template("review_analysis")
    normalized = _normalized(source)

    assert (
        "Choose `status: completed` when the submitted review is thorough and correct "
        "across every applicable, evaluatable dimension."
    ) in normalized
    assert (
        "Choose `status: failed` only when the submitted review itself is observably "
        "unusable or substantially incomplete"
    ) in normalized
    assert "review is clean and thorough across ALL dimensions" not in normalized
    assert "when the review found major incompleteness" not in normalized


def test_analysis_format_docs_match_prompt_judgment_semantics() -> None:
    planning_doc = load_bundled_format_doc("planning_analysis_decision")
    review_doc = load_bundled_format_doc("review_analysis_decision")

    assert planning_doc is not None
    assert review_doc is not None
    assert "product criteria" in planning_doc.lower()
    assert (
        "The submitted review is direct evidence of what the reviewer covered, "
        "reported, and classified."
    ) in _normalized(review_doc)
    assert "Statuses grade the submitted review, never the implementation." in _normalized(
        review_doc
    )


def test_analysis_examples_keep_the_judged_artifact_as_the_subject() -> None:
    planning_example = load_bundled_example("planning_analysis_decision")
    development_example = load_bundled_example("development_analysis_decision")
    review_example = load_bundled_example("review_analysis_decision")

    assert planning_example is not None
    assert development_example is not None
    assert review_example is not None
    assert "The plan" in planning_example
    assert "Implementation" in development_example
    assert "The submitted review" in review_example
