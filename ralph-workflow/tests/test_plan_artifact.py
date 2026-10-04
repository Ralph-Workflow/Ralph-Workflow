"""Black-box coverage for plan markdown artifacts.

The plan spec is sanity-only. This module tests the new contract
through the public parser entry point: prose plans and structure
that does not match the legacy Pydantic schema are accepted, and
no extraction produces an error.
"""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.plan import is_noop_plan


def _plan_document() -> str:
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


def test_plan_markdown_maps_to_the_canonical_execution_model() -> None:
    content, diagnostics = parse_and_validate(_plan_document(), get_spec("plan"))

    assert not any(item.severity == "error" for item in diagnostics)
    steps = content.get("steps", [])
    assert [step["id"] for step in steps] == ["S-1", "S-2"]
    assert steps[1]["depends_on"] == ["S-1"]
    assert is_noop_plan(content) is False


def test_plan_markdown_accepts_prose_plans_without_steps() -> None:
    """A prose plan with no step blocks is accepted (best-effort)."""
    prose = """---
type: plan
---
This is a substantive prose plan with no headings and more than ten
words so it clears the readability floor. The implementation will
touch the spec, the validation module, and the tool side.
"""
    content, diagnostics = parse_and_validate(prose, get_spec("plan"))

    assert not any(item.severity == "error" for item in diagnostics)
    assert content == {}


def test_plan_markdown_accepts_dangling_dependencies() -> None:
    """A dangling dependency is best-effort, not an error."""
    invalid = _plan_document().replace("Depends on: S-1", "Depends on: S-99")

    content, diagnostics = parse_and_validate(invalid, get_spec("plan"))

    assert not any(item.severity == "error" for item in diagnostics)
    assert content.get("steps")


def test_shipped_two_unit_example_is_accepted_as_a_plan() -> None:
    """The format-doc example with two units round-trips without errors."""
    from importlib import import_module
    from pathlib import Path

    example_path = Path(__file__).resolve().parent.parent / (
        "ralph/mcp/artifacts/format_docs/examples/plan.md"
    )
    content = example_path.read_text(encoding="utf-8")
    import_module("ralph.mcp.artifacts.markdown.specs")
    spec = get_spec("plan")
    parsed, diagnostics = parse_and_validate(content, spec)
    assert not any(item.severity == "error" for item in diagnostics)
    # The format-doc example may or may not declare units; either way
    # the submission is accepted.
    assert isinstance(parsed, dict)
