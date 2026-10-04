"""PLAN001 sanity boundary tests."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs.plan import PLAN_SPEC, analyze_plan_document


def _errors(text: str) -> list[str]:
    _content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    return [diagnostic.rule_id for diagnostic in diagnostics if diagnostic.severity == "error"]


@pytest.mark.parametrize(
    "text",
    ["", "one two three four five six seven eight nine", "\x00" + "\x01" * 100],
)
def test_plan001_rejects_empty_short_or_binary_like_text(text: str) -> None:
    assert _errors(text) == ["PLAN001"]


@pytest.mark.parametrize(
    "text",
    [
        "one two three four five six seven eight nine ten",
        "---\ntype: plan\ntype: plan\nbad metadata\n---\n"
        "one two three four five six seven eight nine ten",
        "---\ntype: plan\nnoop: true\n---\nNo changes are needed because the requested behavior already works correctly.",
    ],
)
def test_sane_or_explicit_noop_text_is_accepted_without_shape_diagnostics(text: str) -> None:
    assert _errors(text) == []


def test_obvious_refusal_and_placeholder_are_rejected() -> None:
    assert _errors("I cannot complete this request because policy stops me from planning it today") == [
        "PLAN001"
    ]
    assert _errors("TODO: plan goes here after the next planning pass with all required details") == [
        "PLAN001"
    ]


def test_analyze_plan_document_preserves_sanity_result() -> None:
    content, diagnostics, overrides = analyze_plan_document("one two three four five six seven eight nine ten")
    assert content == {}
    assert diagnostics == []
    assert overrides == []
