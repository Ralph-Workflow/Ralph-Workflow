"""Black-box coverage for plan markdown artifacts."""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.plan import is_noop_plan
from tests._support.typed_accessors import must_dict_list
from tests.mcp.test_md_plan_spec import _plan_document


def test_plan_markdown_maps_to_the_canonical_execution_model() -> None:
    content, diagnostics = parse_and_validate(_plan_document(), get_spec("plan"))

    assert diagnostics == []
    steps = must_dict_list(content["steps"])
    assert [step["number"] for step in steps] == [1, 2]
    assert steps[0]["targets"] == [
        {"path": "ralph/mcp/artifacts/markdown/specs/plan.py", "action": "modify"},
        {"path": "tests/mcp/test_md_plan_spec.py", "action": "create"},
    ]
    assert steps[1]["depends_on"] == [1]
    assert is_noop_plan(content) is False


def test_plan_markdown_accepts_steps_without_the_recommended_optional_sections() -> None:
    truncated = (
        _plan_document().split("## Critical Files", maxsplit=1)[0].replace("Satisfies: AC-01\n", "")
    )

    content, diagnostics = parse_and_validate(truncated, get_spec("plan"))

    assert content["steps"]
    assert diagnostics == []


def test_plan_markdown_accepts_dangling_stable_id_references() -> None:
    invalid = _plan_document().replace("Depends on: S-1", "Depends on: S-99")

    content, diagnostics = parse_and_validate(invalid, get_spec("plan"))

    assert content["steps"]
    assert diagnostics == []


def test_shipped_two_unit_example_validates_as_a_plan_with_work_units() -> None:
    """S-8: the format-doc example exercises a two-unit Work Units plan.

    A compact two-unit plan with disjoint directories, no reserved
    paths, and unit count within the cap must round-trip through the
    validator with zero error-severity diagnostics. The example is
    bundled under ``format_docs/examples/plan.md`` so the format-doc
    test infrastructure can pick it up alongside the linear fixture.
    """
    from importlib import import_module
    from pathlib import Path

    example_path = Path(__file__).resolve().parent.parent / (
        "ralph/mcp/artifacts/format_docs/examples/plan.md"
    )
    content = example_path.read_text(encoding="utf-8")
    import_module("ralph.mcp.artifacts.markdown.specs")
    spec = get_spec("plan")
    parsed, diagnostics = parse_and_validate(content, spec)
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == [], "; ".join(f"line {d.line} [{d.rule_id}] {d.message}" for d in errors)
    work_units = parsed.get("work_units")
    assert work_units, "shipped example must declare ## Work Units"
    assert len(work_units) == 2
    # Disjoint directories: per-unit allowlists must not overlap.
    dirs = sorted(unit["allowed_directories"] for unit in work_units)
    assert dirs == [["src/auth"], ["tests/auth"]]
