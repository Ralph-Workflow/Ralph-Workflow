"""Regression tests: review.jinja examples stay aligned with the issues markdown grammar."""

from __future__ import annotations

import re
from pathlib import Path

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import ISSUES_SPEC

_REVIEW_TEMPLATE_PATH = (
    Path(__file__).parent.parent / "ralph" / "prompts" / "templates" / "review.jinja"
)
_FRONTMATTER_EXAMPLE = re.compile(
    r"---\ntype: issues\nstatus: (?:no_issues|issues_found)\n---.*?(?=\n```|\n\{% endset %\})",
    re.DOTALL,
)


def _load_review_template() -> str:
    return _REVIEW_TEMPLATE_PATH.read_text(encoding="utf-8")


def test_template_has_no_retired_clean_status_reference() -> None:
    assert "status: clean" not in _load_review_template()


def test_all_markdown_examples_pass_the_issues_grammar() -> None:
    examples = _FRONTMATTER_EXAMPLE.findall(_load_review_template())

    assert examples, "review.jinja must contain at least one issues markdown example"
    for example in examples:
        _, diagnostics = parse_and_validate(example, ISSUES_SPEC)
        assert diagnostics == []


def test_template_teaches_both_issues_statuses() -> None:
    text = _load_review_template()

    assert "status: no_issues" in text
    assert "status: issues_found" in text
