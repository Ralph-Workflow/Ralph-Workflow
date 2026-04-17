"""Tests for WorkUnit field validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ralph.pipeline.work_units import WorkUnit


@pytest.mark.parametrize(
    ("unit_id", "expected"),
    [
        ("", False),
        ("a b", False),
        ("a/b", False),
        ("../x", False),
        ("a" * 65, False),
        ("a;b", False),
        ("ü", False),
        ("unit-1", True),
        ("task_abc", True),
        ("A1B2", True),
        ("a", True),
        ("a" * 64, True),
    ],
)
def test_unit_id_validator(unit_id: str, expected: bool) -> None:
    if expected:
        unit = WorkUnit(unit_id=unit_id, description="desc")
        assert unit.unit_id == unit_id
    else:
        with pytest.raises(ValidationError, match="unit_id"):
            WorkUnit(unit_id=unit_id, description="desc")


@pytest.mark.parametrize(
    ("allowed_directories", "expected"),
    [
        ([], True),
        (["src"], True),
        (["src/lib", "tests/unit"], True),
        (["/etc"], False),
        (["../x"], False),
        (["a/../b"], False),
        ([""], False),
        (["\\"], False),
    ],
)
def test_allowed_directories_validator(allowed_directories: list[str], expected: bool) -> None:
    if expected:
        unit = WorkUnit(
            unit_id="u1",
            description="desc",
            allowed_directories=allowed_directories,
        )
        assert unit.allowed_directories == allowed_directories
    else:
        with pytest.raises(ValidationError, match="allowed_directories"):
            WorkUnit(
                unit_id="u1",
                description="desc",
                allowed_directories=allowed_directories,
            )
