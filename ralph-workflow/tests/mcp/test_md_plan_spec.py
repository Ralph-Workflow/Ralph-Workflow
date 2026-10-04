"""Black-box contract tests for the sanity-only plan spec.

The plan spec is intentionally permissive: only PLAN001 (not-a-plan)
rejects. Structural fields are best-effort; the test surface here
verifies the contract the spec actually enforces.
"""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC


def _complete_plan() -> str:
    return """---
type: plan
---
## Skills MCP
Skills: test-driven-development

## Steps
### [S-1] Update the plan validator
Change the markdown plan validator and prove the focused behavior.
Type: file_change
Files:
- modify ralph/mcp/artifacts/markdown/specs/plan.py
- create tests/mcp/test_md_plan_spec.py
Verify: uv run pytest -q tests/mcp/test_md_plan_spec.py
Expect: the focused plan-contract tests pass with exit code 0

### [S-2] Verify the complete focused contract
Run the focused plan suites after the validator change.
Type: verify
Depends on: S-1
Verify: uv run pytest -q tests/mcp/test_md_plan_spec.py tests/mcp/test_md_plan_validator_parity.py
Expect: the focused plan suites pass with exit code 0
"""


# Backward-compatible alias used by other test modules.
_plan_document = _complete_plan


def _errors(document: str) -> set[str]:
    _content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    return {item.rule_id for item in diagnostics if item.severity == "error"}


def test_plan_spec_accepts_a_complete_step_document() -> None:
    """A well-formed plan produces no diagnostics and yields its steps."""
    content, diagnostics = parse_and_validate(_complete_plan(), PLAN_SPEC)

    assert diagnostics == []
    steps = content["steps"]
    assert [step["id"] for step in steps] == ["S-1", "S-2"]
    assert steps[1]["depends_on"] == ["S-1"]


def test_plan_spec_accepts_prose_plans_without_step_blocks() -> None:
    """A free-form prose plan is accepted: no steps is not an error."""
    prose = """---
type: plan
---
We will rewrite the plan validator to apply only the sanity check. The
implementation touches the spec, the validation module, and the
tool-side gate. The development phase and analyzer read the raw text.
"""
    content, diagnostics = parse_and_validate(prose, PLAN_SPEC)

    assert diagnostics == []
    assert "steps" not in content


def test_plan_spec_accepts_plans_with_duplicate_step_ids() -> None:
    """Duplicate step IDs are absorbed as best-effort extraction."""
    duplicate = """---
type: plan
---
## Work
### [S-1] First
Type: file_change
Files:
- modify a.py
Verify: pytest tests/test_x.py -q
Expect: tests pass

### [S-1] Duplicate
Type: file_change
Files:
- modify b.py
Verify: pytest tests/test_x.py -q
Expect: tests pass
"""
    content, diagnostics = parse_and_validate(duplicate, PLAN_SPEC)

    assert diagnostics == []
    step_ids = [step["id"] for step in content["steps"]]
    assert step_ids.count("S-1") == 1


def test_plan_spec_accepts_plans_with_dangling_dependencies() -> None:
    """A ``Depends on: S-99`` reference does not reject the plan."""
    dangling = """---
type: plan
---
## Work
### [S-1] First
Type: file_change
Depends on: S-99
Files:
- modify a.py
Verify: pytest tests/test_x.py -q
Expect: tests pass
"""
    content, diagnostics = parse_and_validate(dangling, PLAN_SPEC)

    assert diagnostics == []
    assert content["steps"][0]["depends_on"] == ["S-99"]


def test_plan_spec_accepts_plans_with_dependency_cycles() -> None:
    """A cyclic dependency graph is best-effort, not a structural error."""
    cyclic = """---
type: plan
---
## Work
### [S-1] First
Type: file_change
Depends on: S-2
Files:
- modify a.py
Verify: pytest tests/test_x.py -q
Expect: tests pass

### [S-2] Second
Type: file_change
Depends on: S-1
Files:
- modify b.py
Verify: pytest tests/test_x.py -q
Expect: tests pass
"""
    _content, diagnostics = parse_and_validate(cyclic, PLAN_SPEC)

    assert not any(item.severity == "error" for item in diagnostics)


def test_plan_spec_accepts_plans_with_malformed_step_ids() -> None:
    """Malformed step IDs are absorbed as best-effort non-S-n entries."""
    malformed = """---
type: plan
---
## Work
### [STEP-1] Mistyped
Type: file_change
Files:
- modify a.py
Verify: pytest tests/test_x.py -q
Expect: tests pass
"""
    content, diagnostics = parse_and_validate(malformed, PLAN_SPEC)

    assert not any(item.severity == "error" for item in diagnostics)
    assert content["steps"]


def test_plan_spec_rejects_an_empty_plan() -> None:
    """An empty plan is rejected with PLAN001 (recognizably not a plan)."""
    content, diagnostics = parse_and_validate("", PLAN_SPEC)

    assert content == {}
    assert any(item.rule_id == "PLAN001" and item.severity == "error" for item in diagnostics)


def test_plan_spec_rejects_a_refusal() -> None:
    """A refusal is rejected with PLAN001 (recognizably not a plan)."""
    refusal = (
        "I'm sorry, I cannot help with that. I am an AI assistant "
        "without the ability to plan your project for you in this environment."
    )
    content, diagnostics = parse_and_validate(refusal, PLAN_SPEC)

    assert content == {}
    assert any(item.rule_id == "PLAN001" for item in diagnostics)


def test_noop_is_the_only_step_less_plan_variant() -> None:
    """A canonical noop: true returns ``{"noop": True}`` with no diagnostics."""
    content, diagnostics = parse_and_validate("---\ntype: plan\nnoop: true\n---\n", PLAN_SPEC)

    assert diagnostics == []
    assert content.get("noop") is True


@pytest.mark.parametrize("field", ["type: plan\nschema_version: 1", "type: plan"])
def test_plan_spec_ignores_legacy_schema_version_frontmatter(field: str) -> None:
    """Legacy ``schema_version`` is tolerated (not a structural diagnostic)."""
    if field == "type: plan\nschema_version: 1":
        body = """---
type: plan
schema_version: 1
---
This plan has prose explaining the work we will do and a few details
about the files and tests we will touch. More than ten words here.
"""
    else:
        body = """---
type: plan
---
This plan has prose explaining the work we will do and a few details
about the files and tests we will touch. More than ten words here.
"""
    _content, diagnostics = parse_and_validate(body, PLAN_SPEC)

    assert not any(item.rule_id == "PLAN027" for item in diagnostics)
    assert not any(item.severity == "error" for item in diagnostics)
