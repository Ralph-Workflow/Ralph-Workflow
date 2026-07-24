"""Tests for the plan / analysis-decision artifact readers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.display.artifact_reader import (
    PlanSummary,
    read_latest_analysis_decision,
    read_plan_artifact,
)

if TYPE_CHECKING:
    from collections.abc import Callable

EXPECTED_TOTAL_STEPS = 2


def _plan_document() -> str:
    return """---
type: plan
schema_version: 1
intent_verb: add
---
## Summary
Improve the dashboard

Intent: Show the current execution plan.
Coverage: feature, test

## Scope
- [SC-1] Item A
  Category: feature
- [SC-2] Item B
  Category: feature
- [SC-3] Item C
  Category: test

## Skills MCP
Skills: test-driven-development, verification-before-completion

## Steps

### [S-1] Render the plan
Project the Markdown plan into the display summary.

Type: file_change
Priority: high
Files:
- modify ralph/display/artifact_reader.py

### [S-2] Verify the display
Run the focused display tests.

Type: verify
Depends on: S-1
Verify: pytest tests/unit/display -q

## Critical Files
- [CF-1] ralph/display/artifact_reader.py
  Action: modify
  Changes: load the canonical Markdown plan

## Risks
- [R-1] timeout
  Severity: medium
  Mitigation: Keep transcript output plain-text safe.
- [R-2] manual cleanup may be required
  Severity: low
  Mitigation: Preserve the tolerant missing-artifact behavior.

## Verification
- [V-1] pytest tests/unit/display -q
  Expect: focused tests pass
  Timeout: 10
"""


def _text_loader(content: str | None) -> Callable[[Path], str | None]:
    return lambda _path: content


def _analysis_decision_document(
    artifact_type: str,
    *,
    status: str,
    summary: str,
) -> str:
    return f"""---
type: {artifact_type}
status: {status}
---

## Summary

- [SUM-1] {summary}
"""


def test_read_plan_artifact_missing_returns_none() -> None:
    assert read_plan_artifact(Path("/workspace"), _text_loader=_text_loader(None)) is None


def test_read_plan_artifact_malformed_returns_none() -> None:
    result = read_plan_artifact(
        Path("/workspace"),
        _text_loader=_text_loader("not a plan artifact"),
    )
    assert result is None


def test_read_plan_artifact_projects_context_and_scope() -> None:
    result = read_plan_artifact(
        Path("/workspace"),
        _text_loader=_text_loader(_plan_document()),
    )
    assert isinstance(result, PlanSummary)
    assert result.summary == "Improve the dashboard"
    assert result.scope_items == ("Item A", "Item B", "Item C")
    assert result.total_steps == EXPECTED_TOTAL_STEPS
    assert "timeout" in result.risks_mitigations
    assert "manual cleanup may be required" in result.risks_mitigations


def test_read_latest_analysis_decision_missing_returns_none(tmp_path: Path) -> None:
    assert read_latest_analysis_decision(tmp_path, "development_analysis") is None


def test_read_latest_analysis_decision_loads_canonical_markdown(tmp_path: Path) -> None:
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "development_analysis_decision.md").write_text(
        _analysis_decision_document(
            "development_analysis_decision",
            status="completed",
            summary="All tests passed",
        ),
        encoding="utf-8",
    )

    result = read_latest_analysis_decision(tmp_path, "development_analysis")

    assert result is not None
    assert result.decision == "completed"
    assert result.reason == "All tests passed"


def test_read_latest_analysis_decision_prefers_markdown_over_old_state(tmp_path: Path) -> None:
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "review_analysis_decision.md").write_text(
        _analysis_decision_document(
            "review_analysis_decision",
            status="completed",
            summary="Canonical decision",
        ),
        encoding="utf-8",
    )
    (artifacts / "review_analysis_decision.json").write_text(
        "this must never be parsed",
        encoding="utf-8",
    )

    result = read_latest_analysis_decision(tmp_path, "review_analysis")

    assert result is not None
    assert result.decision == "completed"
    assert result.reason == "Canonical decision"


def test_read_latest_analysis_decision_rejects_json_only_state(tmp_path: Path) -> None:
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "review_analysis_decision.json").write_text(
        '{"type":"review_analysis_decision","content":{"decision":"completed"}}',
        encoding="utf-8",
    )
    (artifacts / "review_analysis.json").write_text(
        '{"type":"review_analysis_decision","content":{"decision":"completed"}}',
        encoding="utf-8",
    )

    assert read_latest_analysis_decision(tmp_path, "review_analysis") is None
