"""Static coherence checks for agent-facing prompt templates."""

from __future__ import annotations

import re

from ralph.mcp.artifacts.format_docs import load_bundled_format_doc
from ralph.mcp.artifacts.markdown._spec import parse_and_validate
from ralph.mcp.artifacts.markdown.specs.plan import PLAN_SPEC
from ralph.prompts.template_context import TemplateContext

PLANNING_ANALYSIS_CORE_WORKFLOW_GUIDANCE = (
    "Infer the complete user-visible workflow required by the request"
)
PLANNING_DEPENDENT_SECTION_CLOSURE_GUIDANCE = (
    "If one finding invalidates another section"
)
PLANNING_EDIT_ADJACENT_ISSUES_GUIDANCE = (
    "search for adjacent issues"
)
PLANNING_EDIT_CLOSURE_LEDGER_GUIDANCE = "closure ledger"
PLANNING_EDIT_FALLBACK_HISTORY_GUIDANCE = "ARTIFACT HISTORY"
PLANNING_EDIT_FALLBACK_SCOPE_CONDITIONAL_GUIDANCE = "repository-wide"
PLANNING_EDIT_FALLBACK_SCOUT_GUIDANCE = "subagent"
PLANNING_SHARED_DEFECT_VOCAB_GUIDANCE = "defect"
PLANNING_STABLE_ID_GUIDANCE = "stable ID"


def _template(name: str) -> str:
    return TemplateContext.default().registry.get_template(name)


def test_planning_templates_name_only_the_markdown_artifact_surface() -> None:
    text = "\n".join(
        _template(name)
        for name in (
            "planning",
            "planning_analysis",
            "planning_edit",
            "planning_edit_fallback",
            "planning_fallback",
        )
    )

    assert "SUBMIT_MD_ARTIFACT_TOOL_REFERENCE" in text
    assert "VERIFY_MD_ARTIFACT_TOOL_REFERENCE" in text
    assert "EDIT_MD_PLAN_STEP_TOOL_REFERENCE" in text
    for retired in (
        "ralph_submit_artifact",
        "ralph_submit_plan_section",
        "ralph_submit_plan_sections",
        "ralph_validate_draft",
        "ralph_finalize_plan",
        "plan.json",
    ):
        assert retired not in text


def test_planning_worked_examples_use_native_step_blocks() -> None:
    examples: list[str] = []
    for name in ("planning.jinja", "planning_fallback.jinja"):
        text = _template(name)
        examples.extend(re.findall(r"```markdown\n(---\ntype: plan\n.*?)(?:\n```)", text, re.DOTALL))

    assert examples
    for example in examples:
        _normalized, diagnostics = parse_and_validate(example, PLAN_SPEC)
        errors = [item for item in diagnostics if item.severity == "error"]
        assert errors == [], f"{errors!r}"
        assert "### [S-" in example
        assert "Depends on:" in example
        assert '{"' not in example


def test_planning_prompts_use_author_facing_plan_vocabulary() -> None:
    text = "\n".join(
        _template(name) for name in ("planning.jinja", "planning_analysis.jinja")
    )

    for label in (
        "## Critical Files",
        "## Parallel Plan",
        "## Work Units",
        "Directories:",
        "Evidence:",
        "Verify:",
    ):
        assert label in text
    assert "Each section entry is one line `- [ID] {json}`" not in text


def test_plan_format_doc_embedded_examples_validate() -> None:
    text = load_bundled_format_doc("plan")
    assert text is not None
    examples = re.findall(r"```markdown\n(---\ntype: plan\n.*?)(?:\n```)", text, re.DOTALL)

    assert examples
    for example in examples:
        _normalized, diagnostics = parse_and_validate(example, PLAN_SPEC)
        errors = [item for item in diagnostics if item.severity == "error"]
        assert errors == [], f"{errors!r}"


def test_planning_edit_templates_explain_stable_targeted_edits() -> None:
    for name in ("planning_edit.jinja", "planning_edit_fallback.jinja"):
        text = _template(name)
        assert "replacement" in text
        assert "### [S-3] Title" in text
        assert "stable" in text.lower()
        assert "never renumbered" in text


def test_analysis_templates_require_markdown_submission_and_actionable_repair() -> None:
    for name in (
        "planning_analysis.jinja",
        "development_analysis.jinja",
        "review_analysis.jinja",
    ):
        text = _template(name)
        assert "SUBMIT_MD_ARTIFACT_TOOL_REFERENCE" in text
        assert ".agent/artifact-formats/" in text
        assert "JSON" not in text


def test_commit_and_development_templates_reference_canonical_markdown_docs() -> None:
    expectations = {
        "commit_message.jinja": "commit_message.md",
        "developer_iteration.jinja": "development_result.md",
        "developer_iteration_continuation.jinja": "development_result.md",
        "commit_cleanup.jinja": "commit_cleanup.md",
    }
    for name, format_doc in expectations.items():
        text = _template(name)
        assert format_doc in text
        assert "SUBMIT_MD_ARTIFACT_TOOL_REFERENCE" in text
