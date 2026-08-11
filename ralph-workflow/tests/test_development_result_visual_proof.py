"""Visual-proof grammar for ``development_result`` artifacts."""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import DEVELOPMENT_RESULT_SPEC


def test_ui_proof_maps_verdict_and_before_after_capture_handles() -> None:
    """A UI proof carries its verdict plus the compared capture handles."""
    content, diagnostics = parse_and_validate(
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
  Verdict ID: verdict-001
  Before Captures: ralph://media/11111111-1111-1111-1111-111111111111
  After Captures: ralph://media/22222222-2222-2222-2222-222222222222
""",
        DEVELOPMENT_RESULT_SPEC,
    )

    assert diagnostics == []
    assert content["plan_items_proven"] == [
        {
            "plan_item": "S-4",
            "proof": "The header UI is capture-backed.",
            "verdict_id": "verdict-001",
            "capture_handles": (
                "ralph://media/11111111-1111-1111-1111-111111111111",
                "ralph://media/22222222-2222-2222-2222-222222222222",
            ),
        }
    ]
