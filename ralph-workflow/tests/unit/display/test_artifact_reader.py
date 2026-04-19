"""Tests for the plan / analysis-decision artifact readers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.display.artifact_reader import (
    PlanSummary,
    read_latest_analysis_decision,
    read_plan_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_TOTAL_STEPS = 2


def _write_plan(workspace: Path, content: dict[str, object]) -> None:
    artifacts = workspace / ".agent" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "plan.json").write_text(json.dumps(content))


def test_read_plan_artifact_missing_returns_none(tmp_path: Path) -> None:
    assert read_plan_artifact(tmp_path) is None


def test_read_plan_artifact_malformed_returns_none(tmp_path: Path) -> None:
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "plan.json").write_text("not json")
    assert read_plan_artifact(tmp_path) is None


def test_read_plan_artifact_projects_context_and_scope(tmp_path: Path) -> None:
    _write_plan(
        tmp_path,
        {
            "content": {
                "summary": {
                    "context": "Improve the dashboard",
                    "scope_items": ["Item A", "Item B", "Item C"],
                },
                "steps": [{"id": 1}, {"id": 2}],
                "risks_mitigations": [
                    {"risk": "timeout"},
                    "manual cleanup may be required",
                ],
            }
        },
    )
    result = read_plan_artifact(tmp_path)
    assert isinstance(result, PlanSummary)
    assert result.summary == "Improve the dashboard"
    assert result.scope_items == ("Item A", "Item B", "Item C")
    assert result.total_steps == EXPECTED_TOTAL_STEPS
    assert "timeout" in result.risks_mitigations
    assert "manual cleanup may be required" in result.risks_mitigations


def test_read_plan_artifact_without_content_wrapper(tmp_path: Path) -> None:
    _write_plan(
        tmp_path,
        {
            "summary": {"context": "direct", "scope_items": ["only"]},
            "steps": [{"id": 1}],
        },
    )
    result = read_plan_artifact(tmp_path)
    assert isinstance(result, PlanSummary)
    assert result.summary == "direct"
    assert result.scope_items == ("only",)
    assert result.total_steps == 1


def test_read_latest_analysis_decision_missing_returns_none(tmp_path: Path) -> None:
    assert read_latest_analysis_decision(tmp_path, "development_analysis") is None


def test_read_latest_analysis_decision_prefers_canonical_name(tmp_path: Path) -> None:
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "development_analysis_decision.json").write_text(
        json.dumps(
            {
                "content": {
                    "decision": "proceed",
                    "reason": "all tests passed",
                }
            }
        )
    )
    result = read_latest_analysis_decision(tmp_path, "development_analysis")
    assert result is not None
    assert result.decision == "proceed"
    assert result.reason == "all tests passed"


def test_read_latest_analysis_decision_falls_back_to_drain_json(tmp_path: Path) -> None:
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "review_analysis.json").write_text(
        json.dumps(
            {
                "content": {
                    "decision": "revise",
                    "reason": "needs more tests",
                }
            }
        )
    )
    result = read_latest_analysis_decision(tmp_path, "review_analysis")
    assert result is not None
    assert result.decision == "revise"
    assert result.reason == "needs more tests"
