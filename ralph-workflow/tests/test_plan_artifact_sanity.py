"""Public sanity boundary tests for plan artifacts."""

from __future__ import annotations

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec


def _diagnostic_ids(text: str) -> set[str]:
    _, diagnostics = parse_and_validate(text, get_spec("plan"))
    return {diagnostic.rule_id for diagnostic in diagnostics}


def test_plan_accepts_exactly_ten_words_and_rejects_nine() -> None:
    assert "PLAN001" not in _diagnostic_ids("one two three four five six seven eight nine ten")
    assert "PLAN001" in _diagnostic_ids("one two three four five six seven eight nine")


def test_plan_accepts_malformed_frontmatter_as_prose() -> None:
    content, diagnostics = parse_and_validate(
        "---\ntype: plan\ntype: plan\nnot yaml\n---\n"
        "one two three four five six seven eight nine ten", get_spec("plan")
    )
    assert content == {}
    assert not diagnostics


def test_plan_rejects_control_heavy_and_nul_text() -> None:
    assert "PLAN001" in _diagnostic_ids("\x00" + "\x01" * 100)


def test_plan_deduplicates_step_ids_and_keeps_raw_text() -> None:
    text = """---
type: plan
---
## Work
### [S-1] First
Words sufficient for a readable implementation plan to carry forward.
### [S-1] Duplicate
Words sufficient for another implementation plan section carried forward.
"""
    content, diagnostics = parse_and_validate(text, get_spec("plan"))
    assert not diagnostics
    assert [step["id"] for step in content["steps"]] == ["S-1"]
