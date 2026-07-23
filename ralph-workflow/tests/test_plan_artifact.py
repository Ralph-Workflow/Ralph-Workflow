"""Black-box coverage for plan markdown artifacts."""

from __future__ import annotations

from typing import cast

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.plan import is_noop_plan
from tests.mcp.test_md_plan_spec import _plan_document


def test_plan_markdown_maps_to_the_canonical_execution_model() -> None:
    content, diagnostics = parse_and_validate(_plan_document(), get_spec("plan"))

    assert diagnostics == []
    steps = cast("list[dict[str, object]]", content["steps"])
    assert [step["number"] for step in steps] == [1, 2]
    assert steps[0]["targets"] == [
        {"path": "ralph/mcp/artifacts/markdown/specs/plan.py", "action": "modify"},
        {"path": "tests/mcp/test_md_plan_spec.py", "action": "create"},
    ]
    assert steps[1]["depends_on"] == [1]
    skills_mcp = cast("dict[str, object]", content["skills_mcp"])
    assert skills_mcp["skills"] == ["test-driven-development"]
    assert is_noop_plan(content) is False


def test_plan_markdown_rejects_missing_required_sections_with_line_diagnostics() -> None:
    truncated = _plan_document().split("## Critical Files", maxsplit=1)[0]

    content, diagnostics = parse_and_validate(truncated, get_spec("plan"))

    assert content == {}
    errors = [item for item in diagnostics if item.severity == "error"]
    assert errors
    assert {"Critical Files", "Risks", "Verification"} <= {
        item.section for item in errors if item.rule_id == "SPEC008"
    }
    assert all(item.line > 0 for item in errors)


def test_plan_markdown_rejects_dangling_stable_id_references() -> None:
    invalid = _plan_document().replace("Depends on: S-1", "Depends on: S-99")

    content, diagnostics = parse_and_validate(invalid, get_spec("plan"))

    assert content == {}
    assert any(
        item.rule_id == "PLAN021"
        and item.section == "Steps"
        and "S-99" in item.message
        for item in diagnostics
    )
