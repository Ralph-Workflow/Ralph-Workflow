from __future__ import annotations

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "Fix it.",
        "## Heading",
        "---\n",
        "---\ntype: issues\n---\n",
        "---\ntype: plan\ntype: plan\n---\n",
        "TODO",
        "Working on it",
        "Could you clarify?",
        "Traceback (most recent call last):",
        "## Steps\n### [STEP-1] Fix\nDepends on: S-99\nVerify:",
        "## Steps\n### [S-1] Fix\nDepends on: S-1",
        "## Work\n```python\nunfinished(",
        "Unicode plan: 検証・API — données",
    ],
)
def test_plan_regression_all_text_shapes_are_accepted(text: str) -> None:
    content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    analyzed, analyzed_diagnostics, overrides = analyze_plan_document(text)

    assert isinstance(content, dict)
    assert diagnostics == []
    assert analyzed == content
    assert analyzed_diagnostics == []
    assert overrides == []


@pytest.mark.parametrize("control", ["\x00", "\x01", "\x08", "\x1b"])
def test_plan_regression_binary_control_characters_are_rejected(control: str) -> None:
    content, diagnostics = parse_and_validate("Fix the bug" + control, PLAN_SPEC)

    assert content == {}
    assert len(diagnostics) == 1
    assert diagnostics[0].severity == "error"
    assert "binary" in diagnostics[0].message


def test_plan_text_whitespace_is_preserved_as_valid_text() -> None:
    _, diagnostics = parse_and_validate("Fix\tit\nthen verify\r\n", PLAN_SPEC)

    assert diagnostics == []
