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
    proof = PlanItemProof(
        plan_item="Step 1: Add validation", disposition="completed", proof="Evidence"
    )

    assert proof.plan_item == "Step 1: Add validation"


def test_plan_item_proof_rejects_empty_plan_item() -> None:
    with pytest.raises(ValidationError):
        PlanItemProof(plan_item="", disposition="completed", proof="e")


def test_plan_item_proof_rejects_empty_proof() -> None:
    with pytest.raises(ValidationError):
        PlanItemProof(plan_item="Step 1: Add validation", disposition="completed", proof="")


def test_plan_item_proof_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PlanItemProof.model_validate(
            {
                "plan_item": "Step 1: Add validation",
                "disposition": "completed",
                "proof": "Evidence",
                "extra": "x",
            }
        )


@pytest.mark.parametrize("disposition", ("completed", "adapted", "not_applicable", "blocked"))
def test_plan_item_proof_accepts_closed_disposition_vocabulary(disposition: str) -> None:
    values = {
        "plan_item": "S-1",
        "disposition": disposition,
        "proof": "Re-derivable evidence.",
    }
    if disposition != "completed":
        values["rationale"] = "The workspace evidence supports this disposition."

    proof = PlanItemProof.model_validate(values)

    assert proof.disposition == disposition


def test_plan_item_proof_requires_disposition() -> None:
    with pytest.raises(ValidationError, match="disposition"):
        PlanItemProof(plan_item="S-1", proof="Evidence")


def test_plan_item_proof_rejects_unknown_disposition() -> None:
    with pytest.raises(ValidationError, match="disposition"):
        PlanItemProof.model_validate(
            {"plan_item": "S-1", "disposition": "skipped", "proof": "Evidence"}
        )


@pytest.mark.parametrize("disposition", ("adapted", "not_applicable", "blocked"))
def test_non_completed_plan_item_proof_requires_rationale(disposition: str) -> None:
    with pytest.raises(ValidationError, match="rationale"):
        PlanItemProof.model_validate(
            {"plan_item": "S-1", "disposition": disposition, "proof": "Evidence"}
        )


def test_analysis_item_proof_validates_with_valid_fields() -> None:
    proof = AnalysisItemProof(how_to_fix_item="Add test for edge case", proof="Evidence")

    assert proof.how_to_fix_item == "Add test for edge case"


def test_analysis_item_proof_rejects_empty_finding_id() -> None:
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
        plan_items_proven=[
            PlanItemProof(
                plan_item="Step 1: Add validation",
                disposition="completed",
                proof="Evidence",
            )
        ],
        analysis_items_addressed=[
            AnalysisItemProof(how_to_fix_item="Add test for edge case", proof="Evidence")
        ],
    )

    assert result.plan_items_proven[0].plan_item == "Step 1: Add validation"
    assert result.analysis_items_addressed[0].how_to_fix_item == "Add test for edge case"


def test_development_result_defaults_to_empty_proof_lists() -> None:
    result = DevelopmentResult(status="completed", summary="s", files_changed="f")

    assert result.plan_items_proven == []
    assert result.analysis_items_addressed == []


def test_completed_development_result_rejects_blocked_plan_item() -> None:
    with pytest.raises(ValidationError, match="blocked"):
        DevelopmentResult(
            status="completed",
            summary="s",
            files_changed="f",
            plan_items_proven=[
                PlanItemProof(
                    plan_item="S-1",
                    disposition="blocked",
                    rationale="Required authority is unavailable.",
                    proof="The broker denied the required operation.",
                )
            ],
        )


def test_partial_development_result_accepts_blocked_plan_item() -> None:
    result = DevelopmentResult(
        status="partial",
        plan_items_proven=[
            PlanItemProof(
                plan_item="S-1",
                disposition="blocked",
                rationale="Required authority is unavailable.",
                proof="The broker denied the required operation.",
            )
        ],
    )

    assert result.plan_items_proven[0].disposition == "blocked"


def test_normalize_development_result_accepts_completed_payload() -> None:
    normalized = normalize_development_result_content(
        {
            "status": "completed",
            "summary": "Finished the requested MCP hardening work.",
            "files_changed": "- ralph/mcp/tool_bridge.py",
        }
    )

    assert normalized["status"] == "completed"


def test_normalize_development_result_accepts_partial_without_continuation() -> None:
    normalized = normalize_development_result_content(
        {
            "status": "partial",
            "summary": "Half complete.",
            "files_changed": "- ralph/mcp/tool_bridge.py",
            "next_steps": "Finish the remaining test updates.",
        }
    )

    assert normalized["status"] == "partial"
    assert "continuation" not in normalized


def test_normalize_development_result_accepts_bare_partial_status() -> None:
    normalized = normalize_development_result_content({"status": "partial"})

    assert normalized == {
        "status": "partial",
        "summary": "",
        "files_changed": "",
        "plan_items_proven": [],
        "analysis_items_addressed": [],
    }


def test_normalize_development_result_accepts_bare_failed_status() -> None:
    normalized = normalize_development_result_content({"status": "failed"})

    assert normalized == {
        "status": "failed",
        "summary": "",
        "files_changed": "",
        "plan_items_proven": [],
        "analysis_items_addressed": [],
    }


def test_normalize_development_result_rejects_completed_without_summary() -> None:
    with pytest.raises(DevelopmentResultValidationError, match="summary"):
        normalize_development_result_content(
            {"status": "completed", "files_changed": "- ralph/mcp/tool_bridge.py"}
        )


def test_normalize_development_result_rejects_completed_without_files_changed() -> None:
    with pytest.raises(DevelopmentResultValidationError, match="files_changed"):
        normalize_development_result_content({"status": "completed", "summary": "Done."})


def test_normalize_development_result_still_rejects_unknown_status() -> None:
    with pytest.raises(DevelopmentResultValidationError, match="completed"):
        normalize_development_result_content({"status": "done", "summary": "Done."})
