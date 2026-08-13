"""Pure behavior tests for the development-result markdown artifact specification."""

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs import DEVELOPMENT_RESULT_SPEC


def test_development_result_spec_maps_proof_ids_and_registers() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Implemented the markdown artifact spec.
## Files Changed
- [F-1] ralph/mcp/artifacts/markdown/specs/development_result.py
## Plan Items Proven
- [S-1] Added the development-result mapping and validation.
  Disposition: adapted
  Rationale: The existing parser required an alternate mapping seam.
## Analysis Items Addressed
- [H-1] Added focused pure unit coverage.
## Next Steps
- [N-1] Run the remaining verification.
## Continuation
- [C-1] session-123
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["plan_items_proven"] == [
        {
            "plan_item": "S-1",
            "disposition": "adapted",
            "rationale": "The existing parser required an alternate mapping seam.",
            "proof": "Added the development-result mapping and validation.",
        }
    ]
    assert content["analysis_items_addressed"] == [
        {"how_to_fix_item": "H-1", "proof": "Added focused pure unit coverage."}
    ]
    assert content["continuation"] == {"prior_session_id": "session-123"}
    assert get_spec("development_result") is DEVELOPMENT_RESULT_SPEC


def test_development_result_rejects_unknown_status() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: uncertain
---
## Summary
- [SUM-1] Completed the work.
## Files Changed
- [F-1] src/example.py
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert content == {}
    assert any(
        diagnostic.severity == "error"
        and "completed" in diagnostic.message
        and "partial" in diagnostic.message
        for diagnostic in diagnostics
    )


def test_development_result_accepts_partial_without_next_steps_or_continuation() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Work is incomplete.
## Files Changed
- [F-1] src/example.py
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "partial"
    assert "next_steps" not in content
    assert "continuation" not in content


def test_development_result_accepts_failed_free_form_handoff() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: failed
---
## Summary
- [SUM-1] The required environment could not be brought up.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "failed"


@pytest.mark.parametrize("status", ("partial", "failed"))
def test_non_completed_result_ignores_incidental_proof_shaped_sections(status: str) -> None:
    content, diagnostics = parse_and_validate(
        f"""---
type: development_result
status: {status}
---
## Summary
- [SUM-1] Work stopped early.
## Plan Items Proven
- [S-1] Progress made without completed-proof metadata.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == status
    assert content["plan_items_proven"] == []


def test_development_result_treats_a_partial_body_as_free_form() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
---
I ran out of budget before the refactor landed.

## Where I Stopped
The rename is half applied; nothing else here follows the schema.
- a plain bullet without a stable ID
- another one

## Summary
- [SUM-1] First summary item.
- [SUM-2] A second item the completed grammar would reject.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "partial"
    assert content["summary"] == "First summary item."
    assert content["files_changed"] == ""


def test_development_result_keeps_the_completed_body_strict() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Completed the work.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert content == {}
    assert any(
        diagnostic.severity == "error" and "Files Changed" in diagnostic.message
        for diagnostic in diagnostics
    )


def test_completed_development_result_requires_plan_item_disposition() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Completed the work.
## Files Changed
- [F-1] src/example.py
## Plan Items Proven
- [S-1] Evidence without a disposition.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert content == {}
    assert any("disposition" in diagnostic.message.lower() for diagnostic in diagnostics)


def test_completed_development_result_rejects_blocked_disposition() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Completed the work.
## Files Changed
- [F-1] src/example.py
## Plan Items Proven
- [S-1] The required operation cannot run.
  Disposition: blocked
  Rationale: Required authority is unavailable.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert content == {}
    assert any("blocked" in diagnostic.message.lower() for diagnostic in diagnostics)


@pytest.mark.parametrize("status", ("partial", "failed"))
def test_non_completed_result_requires_summary_section(status: str) -> None:
    """A partial/failed result without a Summary section fails validation.

    The Summary is the concise reason the operator and next iteration
    need to triage the outcome. Silent omission is rejected mechanically.
    """
    content, diagnostics = parse_and_validate(
        f"""---
type: development_result
status: {status}
---
The work could not be completed in time.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert content == {}
    assert any("summary" in diagnostic.message.lower() for diagnostic in diagnostics)


def test_partial_with_summary_and_incomplete_work_passes() -> None:
    """A warned partial result with Summary and honest incomplete-work detail passes."""
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Completed items S-1 through S-3; S-4 needs more iteration time.
## Files Changed
- [F-1] src/feature.py
## Incomplete Work
- [S-4] Rename API endpoints: half-applied, tests not updated.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "partial"
    assert "S-4" in content["summary"]


def test_warned_partial_without_incomplete_work_is_rejected() -> None:
    """cycle_timebox_warned: true without Incomplete Work section is rejected."""
    _content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
cycle_timebox_warned: true
---
## Summary
- [SUM-1] Ran out of time before finishing S-4.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics != []
    assert any("Incomplete Work" in d.message for d in diagnostics)


def test_warned_partial_with_incomplete_work_passes() -> None:
    """cycle_timebox_warned: true with complete Incomplete Work items passes."""
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
cycle_timebox_warned: true
---
## Summary
- [SUM-1] Completed S-1 through S-3; S-4 interrupted by deadline.
## Incomplete Work
- [S-4] Rename API endpoints: half-applied.
  Reason: Tests not updated; only half the endpoints renamed.
  Evidence: tests/test_api.py:42 still references the old endpoint names.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["status"] == "partial"
    assert content["incomplete_work"] == [
        "[S-4] Rename API endpoints: half-applied."
    ]


def test_warned_partial_without_reason_is_rejected() -> None:
    """cycle_timebox_warned Incomplete Work item without Reason is rejected."""
    _content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
cycle_timebox_warned: true
---
## Summary
- [SUM-1] S-4 incomplete.
## Incomplete Work
- [S-4] Rename API endpoints: half-applied.
  Evidence: tests/test_api.py:42 references the old endpoint names.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics != []
    assert any("Reason" in d.message for d in diagnostics)


def test_warned_partial_without_evidence_is_rejected() -> None:
    """cycle_timebox_warned Incomplete Work item without Evidence is rejected."""
    _content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
cycle_timebox_warned: true
---
## Summary
- [SUM-1] S-4 incomplete.
## Incomplete Work
- [S-4] Rename API endpoints: half-applied.
  Reason: Tests not updated.
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics != []
    assert any("Evidence" in d.message for d in diagnostics)
