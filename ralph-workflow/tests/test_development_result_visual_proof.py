"""Visual-proof grammar for ``development_result`` artifacts."""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import DEVELOPMENT_RESULT_SPEC


def test_ui_proof_maps_verdict_and_before_after_capture_handles() -> None:
    """A UI proof carries its verdict plus the compared capture handles."""
    parsed, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Completed the visual change.
## Files Changed
- [F-1] src/ui/header.tsx
## Plan Items Proven
- [S-4] The header UI is capture-backed.
  Disposition: completed
  Verdict ID: verdict-001
  Before Captures: ralph://media/11111111-1111-1111-1111-111111111111
  After Captures: ralph://media/22222222-2222-2222-2222-222222222222
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert parsed["plan_items_proven"] == [
        {
            "plan_item": "S-4",
            "disposition": "completed",
            "proof": "The header UI is capture-backed.",
            "verdict_id": "verdict-001",
            "capture_handles": (
                "ralph://media/11111111-1111-1111-1111-111111111111",
                "ralph://media/22222222-2222-2222-2222-222222222222",
            ),
        }
    ]


def test_ui_proof_does_not_map_handles_without_both_capture_labels() -> None:
    """A UI proof cannot substitute both compared sets into one labeled field."""
    _, diagnostics = parse_and_validate(
        """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Completed the visual change.
## Files Changed
- [F-1] src/ui/header.tsx
## Plan Items Proven
- [S-4] The header UI is capture-backed.
  Disposition: completed
  Verdict ID: verdict-001
  Before Captures: ralph://media/11111111-1111-1111-1111-111111111111, ralph://media/22222222-2222-2222-2222-222222222222
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics and "capture_handles" in diagnostics[0].message


def test_ui_gate_is_shape_independent_reference_label_does_not_trigger() -> None:
    """Changing only the bracketed reference label never changes acceptance.

    A proof that claims the UI work requires the design-evidence fields
    regardless of the label; an identical document whose proof text does
    not claim UI work needs none of them — the label never decides.
    """
    from pytest import raises

    from ralph.mcp.artifacts.development_result import (
        DevelopmentResultValidationError,
        normalize_development_result_content,
    )

    def _content(label: str) -> dict[str, object]:
        return {
            "status": "completed",
            "summary": "Completed the visual change.",
            "files_changed": "src/ui/header.tsx",
            "plan_items_proven": [
                {
                    "plan_item": label,
                    "disposition": "completed",
                    "proof": "The header UI is capture-backed.",
                }
            ],
            "analysis_items_addressed": [],
        }

    with raises(DevelopmentResultValidationError, match="verdict_id"):
        normalize_development_result_content(_content("design"))
    with raises(DevelopmentResultValidationError, match="verdict_id"):
        normalize_development_result_content(_content("S-4"))

    non_ui = _content("S-4")
    non_ui["plan_items_proven"] = [
        {
            "plan_item": "S-4",
            "disposition": "completed",
            "proof": "Ran pytest tests/ -q; exit 0.",
        }
    ]
    assert normalize_development_result_content(non_ui) is not None
