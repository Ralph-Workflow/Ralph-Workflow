"""Tests for structured development_result artifact validation."""

from __future__ import annotations

from importlib import import_module

import pytest
from pydantic import ValidationError

from ralph.mcp.artifacts.development_result import (
    AnalysisItemProof,
    DevelopmentResult,
    DevelopmentResultValidationError,
    PlanItemProof,
    normalize_development_result_content,
)
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec


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


# --- S-7: optional Unplanned Work section ---


def test_normalize_development_result_carries_unplanned_work_through() -> None:
    """`unplanned_work` is a sanctioned field for mid-phase discoveries.

    Bulleted items entered as `## Unplanned Work` round-trip into the
    normalized payload under their own key and never spill into the
    proof arrays: a plan step ID coincidentally equal to an item
    bracket (e.g. ``UW-1``) does not become a proof.
    """
    normalized = normalize_development_result_content(
        {
            "status": "completed",
            "summary": "Done.",
            "files_changed": "- ralph/mcp/tool_bridge.py",
            "unplanned_work": [
                "[UW-1] ralph/mcp/tool_bridge.py:78 — lock contention surfaced "
                "during the refresh-token test; reproduced before the fix.",
            ],
        }
    )

    assert normalized["unplanned_work"] == [
        "[UW-1] ralph/mcp/tool_bridge.py:78 — lock contention surfaced "
        "during the refresh-token test; reproduced before the fix.",
    ]
    assert normalized["plan_items_proven"] == []
    assert normalized["analysis_items_addressed"] == []


def test_normalize_development_result_omits_unplanned_work_when_empty() -> None:
    """Empty ``unplanned_work`` is stripped so the payload matches the
    pre-S-7 shape for plans that did not record a mid-phase discovery."""
    normalized = normalize_development_result_content(
        {
            "status": "completed",
            "summary": "Done.",
            "files_changed": "- ralph/mcp/tool_bridge.py",
            "unplanned_work": [],
        }
    )

    assert "unplanned_work" not in normalized


def test_unplanned_work_markdown_section_validates_with_zero_section_errors() -> None:
    """A ``## Unplanned Work`` section in the markdown validates cleanly.

    The section is documented in the format doc as optional and
    bulleted; its bracketed IDs are not proof IDs, so the spec must
    not raise ``unknown section`` or any other shape diagnostic.
    """
    import_module("ralph.mcp.artifacts.markdown.specs")
    spec = get_spec("development_result")
    content = (
        "---\n"
        "type: development_result\n"
        "status: completed\n"
        "---\n"
        "\n"
        "## Summary\n"
        "\n"
        "- [SUM-1] Implemented the requested change.\n"
        "\n"
        "## Files Changed\n"
        "\n"
        "- [F-1] ralph/mcp/tool_bridge.py\n"
        "\n"
        "## Plan Items Proven\n"
        "\n"
        "- [S-1] ralph/mcp/tool_bridge.py now contains the change; "
        "pytest tests/test_tool_bridge.py -q passes.\n"
        "  Disposition: completed\n"
        "\n"
        "## Unplanned Work\n"
        "\n"
        "- [UW-1] ralph/mcp/tool_bridge.py:78 — lock contention surfaced "
        "during the refresh-token test; reproduced before the fix.\n"
    )

    _, diagnostics = parse_and_validate(content, spec)
    errors = [d for d in diagnostics if d.severity == "error"]

    assert errors == [], "; ".join(
        f"line {d.line} [{d.rule_id}] {d.message}" for d in errors
    )


def test_unplanned_work_items_are_not_promoted_to_proof_ids() -> None:
    """`## Unplanned Work` items must not appear in the proof arrays.

    The bracketed IDs in this section are anchors (not plan-step
    references), so a development_result that carries a ``UW-1``
    discovery must report it only under ``unplanned_work``.
    """
    import_module("ralph.mcp.artifacts.markdown.specs")
    spec = get_spec("development_result")
    content = (
        "---\n"
        "type: development_result\n"
        "status: completed\n"
        "---\n"
        "\n"
        "## Summary\n"
        "\n"
        "- [SUM-1] Done.\n"
        "\n"
        "## Files Changed\n"
        "\n"
        "- [F-1] ralph/mcp/tool_bridge.py\n"
        "\n"
        "## Unplanned Work\n"
        "\n"
        "- [UW-1] ralph/mcp/tool_bridge.py:78 — mid-phase discovery.\n"
    )

    content_dict, diagnostics = parse_and_validate(content, spec)
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == [], "; ".join(
        f"line {d.line} [{d.rule_id}] {d.message}" for d in errors
    )
    assert content_dict["unplanned_work"] == [
        "[UW-1] ralph/mcp/tool_bridge.py:78 — mid-phase discovery."
    ]
    assert content_dict["plan_items_proven"] == []
    assert content_dict["analysis_items_addressed"] == []
