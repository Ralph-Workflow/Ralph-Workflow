"""Focused tests for analysis-decision Markdown contracts."""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec


def _decision(shortfall: str) -> str:
    return f"""---
type: planning_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] The plan needs correction.
## What Came Up Short
- [PA-001] {shortfall}
## How To Fix
- [PA-001] Replace the affected plan text with the repository-grounded correction.
"""


def test_request_changes_requires_a_step_or_plan_level_target() -> None:
    content, diagnostics = parse_and_validate(
        _decision("The rollout risk is unaddressed."),
        get_spec("planning_analysis_decision"),
    )

    assert content == {}
    assert [(item.rule_id, item.severity) for item in diagnostics] == [("ANALYSIS004", "error")]


def test_non_planning_request_changes_do_not_require_a_plan_step_target() -> None:
    document = _decision("The implementation omits a required negative test.").replace(
        "planning_analysis_decision", "development_analysis_decision"
    )

    content, diagnostics = parse_and_validate(document, get_spec("development_analysis_decision"))

    assert diagnostics == []
    assert content["finding_targets"] == {}


def test_request_changes_preserves_exact_step_binding() -> None:
    content, diagnostics = parse_and_validate(
        _decision("Step: [S-2] lacks an executable verification command."),
        get_spec("planning_analysis_decision"),
    )

    assert diagnostics == []
    assert content["step_references"] == ["S-2"]


def test_request_changes_allows_explicit_plan_level_target() -> None:
    content, diagnostics = parse_and_validate(
        _decision("Plan-level: The outcome omits an out-of-scope boundary."),
        get_spec("planning_analysis_decision"),
    )

    assert diagnostics == []
    assert content["step_references"] == []
