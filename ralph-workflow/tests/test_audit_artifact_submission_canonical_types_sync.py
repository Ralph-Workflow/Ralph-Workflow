from __future__ import annotations

import re

from ralph.mcp.tools.artifact import _KNOWN_ARTIFACT_TYPES
from ralph.testing.audit_artifact_submission_canonical_path import (
    _CANONICAL_TYPES,
    _FORBIDDEN_PATH_PATTERNS,
)


def test_canonical_types_equals_known_artifact_types() -> None:
    assert _CANONICAL_TYPES == _KNOWN_ARTIFACT_TYPES


def test_known_artifact_types_is_non_empty() -> None:
    assert _KNOWN_ARTIFACT_TYPES


def test_canonical_types_contains_commit_message_and_plan() -> None:
    assert "commit_message" in _CANONICAL_TYPES
    assert "plan" in _CANONICAL_TYPES


def test_review_and_verification_types_are_canonical() -> None:
    assert "review" in _CANONICAL_TYPES
    assert "verification" in _CANONICAL_TYPES


def test_canonical_types_used_in_forbidden_path_patterns() -> None:
    patterns_3_and_4 = [p[0] for p in _FORBIDDEN_PATH_PATTERNS[2:]]
    for t in _CANONICAL_TYPES:
        for pattern in patterns_3_and_4:
            assert re.search(re.escape(t), pattern) is not None, (
                f"Canonical type {t!r} not found in pattern {pattern!r}"
            )
