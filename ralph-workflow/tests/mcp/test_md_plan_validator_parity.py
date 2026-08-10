"""Parity coverage for the plan validation entry points."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document

_COMPLETE_PLAN = """---
type: plan
---
## Steps
### [S-1] Preserve validation parity
Keep the direct validator and the MCP submission validator aligned so complete
plans retain equivalent diagnostics when either supported entry point is used.
Type: file_change
Files:
- modify ralph/mcp/artifacts/markdown/specs/plan.py
Verify: uv run pytest tests/mcp/test_md_plan_validator_parity.py -q
Expect: the validation parity tests pass with exit code 0
"""


@pytest.mark.parametrize(
    "document",
    [
        _COMPLETE_PLAN,
        _COMPLETE_PLAN.rstrip() + "\nThen run the following commands:\n",
        "---\ntype: plan\nnoop: true\n---\n",
        "---\ntype: plan\n---\nI cannot produce the requested plan in this environment.\n",
        """---
type: plan
schema_version: 1
## Outcome
Keep both plan validator entry points aligned when an interrupted metadata
block consumes otherwise complete plan content before the closing delimiter.

### [S-1] Reject swallowed plan bodies
Ensure canonical submission cannot accept this incomplete artifact form.
""",
        """---
type: plan
---
## Intent
This incomplete plan must be rejected before execution.
""",
    ],
    ids=("complete", "truncated", "noop", "refusal", "unterminated_frontmatter", "missing_steps"),
)
def test_plan_regression_validation_entry_points_emit_same_rule_severity_set(
    document: str,
) -> None:
    """S-6: direct and MCP-facing plan validation remain diagnostically equivalent."""
    _content, direct_diagnostics = parse_and_validate(document, PLAN_SPEC)
    _content, analyzed_diagnostics, _overridden = analyze_plan_document(document)

    assert {(item.rule_id, item.severity) for item in direct_diagnostics} == {
        (item.rule_id, item.severity) for item in analyzed_diagnostics
    }
