"""Parity coverage for the plan validation entry points."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document
from ralph.mcp.artifacts.plan import normalize_plan_artifact_content
from ralph.pipeline.work_units import parse_work_units_from_artifact

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


@pytest.mark.parametrize("heading", ["Work Units", "Parallel Plan"])
@pytest.mark.parametrize("ownership_field", ["Directories", "Paths"])
def test_parallel_formats_preserve_executable_ownership_and_dependencies(
    heading: str,
    ownership_field: str,
) -> None:
    if heading == "Work Units" and ownership_field == "Paths":
        ownership_field = "Directories"
    document = "---\ntype: plan\n---\n" + f"## {heading}\n"
    for number, area in enumerate(("contracts", "client", "docs"), start=1):
        document += (
            f"- [U-{number}] Update {area}\n  {ownership_field}: src/{area}/main.py\n"
            f"\n### [S-{number}] Update {area}\n"
            f"Type: file_change\nFiles:\n- modify src/{area}/main.py\n"
            + ("Depends on: S-1\n" if number == 2 else "")
            + f"Verify: pytest tests/{area} -q\nExpect: focused tests pass\n\n"
        )

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    assert diagnostics == []
    plan = parse_work_units_from_artifact(content)
    assert plan is not None
    assert [(unit.unit_id, unit.step_ids, unit.dependencies) for unit in plan.work_units] == [
        ("U-1", ["S-1"], []),
        ("U-2", ["S-2"], ["U-1"]),
        ("U-3", ["S-3"], []),
    ]


@pytest.mark.parametrize("heading", ["Work Units", "Parallel Plan"])
def test_incomplete_parallel_plan_is_accepted_with_repair_advice(heading: str) -> None:
    document = (
        "---\ntype: plan\n---\n"
        f"## {heading}\n- [U-1] Implement independent client behavior\n"
        "### [S-1] Update the client behavior and preserve existing behavior\n"
        "The executor should inspect the client and determine concrete targets and proof.\n"
    )
    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    assert content["steps"][0]["number"] == 1
    assert not any(item.severity == "error" for item in diagnostics)
    assert any(item.severity == "warning" for item in diagnostics)
    normalized = normalize_plan_artifact_content(content)
    assert normalized["steps"][0]["number"] == 1


@pytest.mark.parametrize("heading", ["Work Units", "Parallel Plan"])
def test_parallel_units_can_share_file_responsibility(heading: str) -> None:
    document = "---\ntype: plan\n---\n" + f"## {heading}\n"
    for number, responsibility in enumerate(("retry policy", "error formatting"), start=1):
        document += (
            f"- [U-{number}] Implement {responsibility}\n  Directories: src/client\n"
            f"### [S-{number}] Implement {responsibility}\nType: file_change\n"
            "Files:\n- modify src/client/main.py\n"
            "Verify: pytest tests/client -q\nExpect: client behavior passes\n"
        )
    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    assert not any(item.severity == "error" for item in diagnostics)
    plan = parse_work_units_from_artifact(content)
    assert plan is not None
    assert [(unit.unit_id, unit.step_ids) for unit in plan.work_units] == [
        ("U-1", ["S-1"]),
        ("U-2", ["S-2"]),
    ]
