"""Regression tests: review.jinja must stay aligned with the Issues schema."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ralph.mcp.artifacts.typed_artifacts import (
    TypedArtifactValidationError,
    normalize_issues_content,
)

_REVIEW_TEMPLATE_PATH = (
    Path(__file__).parent.parent / "ralph" / "prompts" / "templates" / "review.jinja"
)

_VALID_STATUSES = frozenset({"issues_found", "no_issues"})


def _load_review_template() -> str:
    return _REVIEW_TEMPLATE_PATH.read_text(encoding="utf-8")


def _extract_json_examples(template_text: str) -> list[dict[str, object]]:
    """Extract JSON objects from fenced code blocks in the template."""
    examples: list[dict[str, object]] = []
    pattern = r"```json\s*\n(.*?)\n```"
    for match in re.finditer(pattern, template_text, re.DOTALL):
        raw = match.group(1).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            examples.append(obj)
    return examples


def _extract_inline_json_examples(template_text: str) -> list[dict[str, object]]:
    """Extract JSON objects embedded in backtick spans within the template text."""
    examples: list[dict[str, object]] = []
    pattern = r"`(\{[^`]+\})`"
    for match in re.finditer(pattern, template_text):
        raw = match.group(1).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "status" in obj:
            examples.append(obj)
    return examples


class TestReviewTemplateStatusValues:
    def test_template_has_no_clean_status_reference(self) -> None:
        text = _load_review_template()
        assert '"clean"' not in text, (
            'review.jinja must not reference status="clean"; '
            'use status="no_issues" instead'
        )

    def test_all_example_statuses_are_valid_schema_values(self) -> None:
        text = _load_review_template()
        inline_examples = _extract_inline_json_examples(text)
        fenced_examples = _extract_json_examples(text)
        all_examples = inline_examples + fenced_examples

        assert all_examples, "review.jinja must contain at least one JSON example"

        for example in all_examples:
            status = example.get("status")
            if status is not None:
                assert status in _VALID_STATUSES, (
                    f"review.jinja example uses status={status!r} which is not "
                    f"in {sorted(_VALID_STATUSES)}"
                )

    def test_clean_case_example_passes_schema_validation(self) -> None:
        clean_example = {
            "status": "no_issues",
            "summary": "No issues found.",
            "issues": [],
            "what_came_up_short": [],
            "how_to_fix": [],
        }
        result = normalize_issues_content(clean_example)
        assert result["status"] == "no_issues"

    def test_issues_found_example_passes_schema_validation(self) -> None:
        issues_example = {
            "status": "issues_found",
            "summary": "Found issues.",
            "issues": [
                {"path": "src/main.py", "severity": "high", "summary": "Missing validation"}
            ],
            "what_came_up_short": ["No input validation"],
            "how_to_fix": ["Add validation"],
        }
        result = normalize_issues_content(issues_example)
        assert result["status"] == "issues_found"

    def test_no_issues_with_empty_arrays_passes_schema(self) -> None:
        payload = {
            "status": "no_issues",
            "summary": "The implementation is clean.",
            "issues": [],
            "what_came_up_short": [],
            "how_to_fix": [],
        }
        result = normalize_issues_content(payload)
        assert result["status"] == "no_issues"

    def test_issues_found_with_empty_remediation_fails_schema(self) -> None:
        bad_payload = {
            "status": "issues_found",
            "summary": "Found issues.",
            "issues": [{"path": "x.py", "severity": "high", "summary": "bug"}],
            "what_came_up_short": [],
            "how_to_fix": [],
        }
        with pytest.raises(TypedArtifactValidationError):
            normalize_issues_content(bad_payload)
