"""Validation failure observability labels."""

from __future__ import annotations

from ralph.recovery.classifier import FailureCategory


def test_artifact_validation_category_has_canonical_value() -> None:
    assert str(FailureCategory.ARTIFACT_VALIDATION) == "artifact_validation"
