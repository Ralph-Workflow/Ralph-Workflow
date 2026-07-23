"""Safe read-only handling for pre-markdown artifact files."""

from __future__ import annotations

import json
from typing import cast

_MIGRATED_TYPES = frozenset({"plan", "planning_analysis_decision", "development_analysis_decision", "review_analysis_decision", "development_result", "product_spec", "issues", "fix_result", "smoke_test_result", "commit_cleanup", "commit_message"})


def parse_or_reject(path: str, text: str) -> dict[str, object]:
    """Return a well-formed legacy envelope or explain how to recover."""
    try:
        value = cast("object", json.loads(text))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Legacy artifact {path} is invalid JSON; re-author it as markdown.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Legacy artifact {path} must be an object; re-author it as markdown.")
    artifact_type = value.get("type")
    content = value.get("content")
    if artifact_type in _MIGRATED_TYPES and isinstance(content, dict):
        return cast("dict[str, object]", value)
    raise ValueError(f"Legacy artifact {path} cannot be safely migrated; re-author it as markdown.")
