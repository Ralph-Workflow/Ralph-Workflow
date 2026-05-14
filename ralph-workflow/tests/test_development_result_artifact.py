"""Tests for structured development_result artifact validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ralph.mcp.artifacts.development_result import (
    AnalysisItemProof,
    DevelopmentResult,
    DevelopmentResultValidationError,
    PlanItemProof,
    normalize_development_result_content,
)


def test_plan_item_proof_validates_with_valid_fields() -> None:
    proof = PlanItemProof(plan_item="Step 1: Add validation", proof="Evidence")

    assert proof.plan_item == "Step 1: Add validation"


def test_plan_item_proof_rejects_empty_plan_item() -> None:
    with pytest.raises(ValidationError):
        PlanItemProof(plan_item="", proof="e")


def test_plan_item_proof_rejects_empty_proof() -> None:
    with pytest.raises(ValidationError):
        PlanItemProof(plan_item="Step 1: Add validation", proof="")


def test_plan_item_proof_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PlanItemProof.model_validate(
            {"plan_item": "Step 1: Add validation", "proof": "Evidence", "extra": "x"}
        )


def test_analysis_item_proof_validates_with_valid_fields() -> None:
    proof = AnalysisItemProof(how_to_fix_item="Add test for edge case", proof="Evidence")

    assert proof.how_to_fix_item == "Add test for edge case"


def test_analysis_item_proof_rejects_empty_how_to_fix_item() -> None:
    with pytest.raises(ValidationError):
        AnalysisItemProof(how_to_fix_item="", proof="Evidence")


def test_analysis_item_proof_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AnalysisItemProof.model_validate(
            {
                "how_to_fix_item": "Add test for edge case",
                "proof": "Evidence",
                "extra": "x",
            }
        )


def test_development_result_accepts_proof_fields() -> None:
    result = DevelopmentResult(
        status="completed",
        summary="Done.",
        files_changed="- src/a.py",
        plan_items_proven=[PlanItemProof(plan_item="Step 1: Add validation", proof="Evidence")],
        analysis_items_addressed=[
            AnalysisItemProof(how_to_fix_item="Add test for edge case", proof="Evidence")
        ],
    )

    assert result.plan_items_proven[0].plan_item == "Step 1: Add validation"


def test_development_result_defaults_to_empty_proof_lists() -> None:
    result = DevelopmentResult(status="completed", summary="s", files_changed="f")

    assert result.plan_items_proven == []
    assert result.analysis_items_addressed == []


def test_normalize_development_result_accepts_completed_payload() -> None:
    normalized = normalize_development_result_content(
        {
            "status": "completed",
            "summary": "Finished the requested MCP hardening work.",
            "files_changed": "- ralph/mcp/tool_bridge.py",
        }
    )

    assert normalized["status"] == "completed"


def test_normalize_development_result_rejects_partial_without_continuation() -> None:
    with pytest.raises(DevelopmentResultValidationError, match="continuation"):
        normalize_development_result_content(
            {
                "status": "partial",
                "summary": "Half complete.",
                "files_changed": "- ralph/mcp/tool_bridge.py",
                "next_steps": "Finish the remaining test updates.",
            }
        )
