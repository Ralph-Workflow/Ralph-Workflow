"""Pure behavior tests for the development-result markdown artifact specification."""

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs import DEVELOPMENT_RESULT_SPEC


def test_development_result_spec_maps_proof_ids_and_registers() -> None:
    content, diagnostics = parse_and_validate(
        """---
type: development_result
status: partial
---
## Summary
- [SUM-1] Implemented the markdown artifact spec.
## Files Changed
- [F-1] ralph/mcp/artifacts/markdown/specs/development_result.py
## Plan Items Proven
- [S-1] Added the development-result mapping and validation.
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
        {"plan_item": "S-1", "proof": "Added the development-result mapping and validation."}
    ]
    assert content["analysis_items_addressed"] == [
        {"how_to_fix_item": "H-1", "proof": "Added focused pure unit coverage."}
    ]
    assert content["continuation"] == {"prior_session_id": "session-123"}
    assert get_spec("development_result") is DEVELOPMENT_RESULT_SPEC


def test_development_result_rejects_unknown_status_and_keeps_partial_requirements_strict() -> None:
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
    partial_content, partial_diagnostics = parse_and_validate(
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

    assert content == {}
    assert any(
        diagnostic.rule_id == "SPEC010"
        and diagnostic.severity == "error"
        and "completed" in diagnostic.message
        and "partial" in diagnostic.message
        for diagnostic in diagnostics
    )
    assert partial_content == {}
    assert any("require next_steps" in diagnostic.message for diagnostic in partial_diagnostics)
