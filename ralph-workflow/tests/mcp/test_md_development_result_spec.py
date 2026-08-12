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
The required environment could not be brought up.
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
