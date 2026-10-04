"""Regression coverage for blocking executor-plan diagnostics."""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document


def _plan() -> str:
    return """---
type: plan
---
## Work
### [S-1] Update validation
Change the validator and prove the focused contract.
Type: file_change
Files:
- modify ralph/mcp/artifacts/markdown/specs/plan.py
Verify: uv run pytest -q tests/mcp/test_md_plan_advisory.py
Expect: the focused contract tests pass with exit code 0
"""


def test_executor_ready_plan_has_no_diagnostics() -> None:
    content, diagnostics = parse_and_validate(_plan(), PLAN_SPEC)

    assert content["steps"]
    assert diagnostics == []


def test_incomplete_step_retains_plan_with_advisory_diagnostics() -> None:
    document = _plan().replace("Files:\n- modify ralph/mcp/artifacts/markdown/specs/plan.py\n", "")

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)

    assert content["steps"][0]["title"] == "Update validation"
    assert not any(item.severity == "error" for item in diagnostics)
    assert any(item.rule_id == "PLAN010" and item.severity == "warning" for item in diagnostics)


def test_validation_overrides_are_rejected_not_recorded() -> None:
    document = _plan() + "\n## Validation Overrides\n- [PLAN010] ignored\n"

    content, diagnostics, overridden = analyze_plan_document(document)

    assert content == {}
    assert overridden == []
    assert any(item.rule_id == "PLAN025" and item.severity == "error" for item in diagnostics)
