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

_PROSE_PLAN = """---
type: plan
---
This is a prose plan with no headings, no step blocks, and more than ten
words so it clears the readability floor. The implementation will touch
several files across the spec, the validation module, and the tools.
"""


@pytest.mark.parametrize(
    "document",
    [
        _COMPLETE_PLAN,
        _PROSE_PLAN,
        "---\ntype: plan\nnoop: true\n---\nNo changes are needed because the requested behavior already works correctly.\n",
        "I'm sorry, I cannot help with that. I am an AI assistant.",
    ],
    ids=("complete", "prose", "noop", "refusal"),
)
def test_plan_regression_validation_entry_points_emit_same_rule_severity_set(
    document: str,
) -> None:
    _content, direct_diagnostics = parse_and_validate(document, PLAN_SPEC)
    _content, analyzed_diagnostics, _overridden = analyze_plan_document(document)

    assert {(item.rule_id, item.severity) for item in direct_diagnostics} == {
        (item.rule_id, item.severity) for item in analyzed_diagnostics
    }


def test_prose_plan_is_accepted_by_both_entry_points() -> None:
    direct_content, direct_diagnostics = parse_and_validate(_PROSE_PLAN, PLAN_SPEC)
    analyzed_content, analyzed_diagnostics, _ = analyze_plan_document(_PROSE_PLAN)

    assert not any(item.severity == "error" for item in direct_diagnostics)
    assert not any(item.severity == "error" for item in analyzed_diagnostics)
    assert "steps" not in direct_content
    assert "steps" not in analyzed_content


def test_explanatory_noop_agrees_across_both_entry_points() -> None:
    direct_content, direct_diagnostics = parse_and_validate(
        "---\ntype: plan\nnoop: true\n---\nNo changes are needed because the requested behavior already works correctly.\n",
        PLAN_SPEC,
    )
    analyzed_content, analyzed_diagnostics, _ = analyze_plan_document(
        "---\ntype: plan\nnoop: true\n---\nNo changes are needed because the requested behavior already works correctly.\n"
    )

    assert direct_diagnostics == []
    assert analyzed_diagnostics == []
    assert direct_content.get("noop") is True
    assert analyzed_content.get("noop") is True
