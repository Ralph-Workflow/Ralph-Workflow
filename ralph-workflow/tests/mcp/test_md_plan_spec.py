"""Black-box contract tests for mandatory plan artifacts."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC


def _plan_document() -> str:
    """Return a valid reusable plan fixture for downstream artifact tests."""
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


def _plan(*, step_type: str = "file_change", fields: str | None = None) -> str:
    body = fields if fields is not None else """Files:
- modify ralph/example.py
Verify: uv run pytest -q tests/mcp/test_md_plan_spec.py
Expect: the focused plan-contract tests pass with exit code 0
"""
    return f"""---
type: plan
---
## Work
### [S-1] Change the plan contract
Update the validator so incomplete plans cannot reach an executor.
Type: {step_type}
{body}"""


def _errors(document: str) -> set[str]:
    _content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    return {item.rule_id for item in diagnostics if item.severity == "error"}


def test_plan_contract_accepts_executor_ready_work_step() -> None:
    content, diagnostics = parse_and_validate(_plan(), PLAN_SPEC)

    assert diagnostics == []
    step = content["steps"][0]
    assert step["number"] == 1
    assert step["title"] == "Change the plan contract"
    assert step["content"] == "Update the validator so incomplete plans cannot reach an executor."
    assert step["targets"] == [{"path": "ralph/example.py", "action": "modify"}]
    assert step["verify_command"] == "uv run pytest -q tests/mcp/test_md_plan_spec.py"
    assert step["expected_outcome"] == "the focused plan-contract tests pass with exit code 0"


@pytest.mark.parametrize(
    ("step_type", "fields", "rule_id"),
    [
        ("unknown", None, "PLAN010"),
        ("file_change", "Verify: uv run pytest -q tests/mcp/test_md_plan_spec.py\nExpect: it passes\n", "PLAN010"),
        ("file_change", "Files:\n- modify ralph/example.py\nExpect: it passes\n", "PLAN020"),
        ("file_change", "Files:\n- modify ralph/example.py\nVerify: run the tests\nExpect: it passes\n", "PLAN020"),
        ("verify", "Verify: uv run pytest -q tests/mcp/test_md_plan_spec.py\n", "PLAN011"),
        ("discovery", "Verify: run the tests\nExpect: it passes\n", "PLAN020"),
    ],
)
def test_plan_contract_rejects_incomplete_step(
    step_type: str, fields: str | None, rule_id: str
) -> None:
    assert rule_id in _errors(_plan(step_type=step_type, fields=fields))


def test_plan_contract_rejects_missing_or_malformed_or_duplicate_step_ids() -> None:
    missing = """---
type: plan
---
## Work
Describe the requested change without any stable step heading.
"""
    malformed = _plan().replace("[S-1]", "[STEP-1]")
    duplicate = _plan() + _plan().split("## Work", 1)[1]

    assert _errors(missing) & {"PLAN001", "PLAN022"}
    assert "PLAN022" in _errors(malformed)
    assert "PLAN022" in _errors(duplicate)


def test_plan_contract_rejects_dangling_and_cyclic_dependencies() -> None:
    dangling = _plan().replace("Verify:", "Depends on: S-2\nVerify:")
    cyclic = _plan() + """
### [S-2] Verify the plan contract
Type: verify
Depends on: S-1
Verify: uv run pytest -q tests/mcp/test_md_plan_spec.py
Expect: the focused plan-contract tests pass with exit code 0
"""
    cyclic = cyclic.replace("Type: file_change", "Type: file_change\nDepends on: S-2")

    assert "PLAN021" in _errors(dangling)
    assert _errors(cyclic)


def test_plan_contract_rejects_legacy_schema_and_override_escape_hatch() -> None:
    assert "PLAN027" in _errors(_plan().replace("type: plan", "type: plan\nschema_version: 1"))
    assert "PLAN025" in _errors(_plan() + "\n## Validation Overrides\n- [PLAN020] x\n")


def test_noop_is_the_only_step_less_plan_variant() -> None:
    content, diagnostics = parse_and_validate("---\ntype: plan\nnoop: true\n---\n", PLAN_SPEC)

    assert diagnostics == []
    assert content == {"noop": True}
