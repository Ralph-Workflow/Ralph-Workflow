"""Tests for planning work_units parsing and validation."""

from __future__ import annotations

from ralph.pipeline.work_units import parse_work_units_from_artifact

WORK_UNIT_COUNT = 2


def test_parse_work_units_returns_none_when_missing() -> None:
    artifact = {"summary": "sequential plan"}

    parsed = parse_work_units_from_artifact(artifact)

    assert parsed is None


def test_parse_work_units_deduplicates_duplicate_unit_ids() -> None:
    artifact = {
        "work_units": [
            {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
            {"unit_id": "u1", "description": "B", "allowed_directories": ["tests"]},
        ]
    }

    parsed = parse_work_units_from_artifact(artifact)

    assert parsed is not None
    assert [unit.unit_id for unit in parsed.work_units] == ["u1"]


def test_parse_work_units_retains_unknown_dependency_for_main_session() -> None:
    artifact = {
        "work_units": [
            {
                "unit_id": "u1",
                "description": "A",
                "allowed_directories": ["src"],
                "dependencies": ["u2"],
            }
        ]
    }

    parsed = parse_work_units_from_artifact(artifact)

    assert parsed is not None
    assert parsed.work_units[0].dependencies == ["u2"]


def test_parse_work_units_retains_cycle_without_rejecting_plan() -> None:
    artifact = {
        "work_units": [
            {
                "unit_id": "u1",
                "description": "A",
                "allowed_directories": ["src"],
                "dependencies": ["u2"],
            },
            {
                "unit_id": "u2",
                "description": "B",
                "allowed_directories": ["tests"],
                "dependencies": ["u1"],
            },
        ]
    }

    parsed = parse_work_units_from_artifact(artifact)

    assert parsed is not None
    assert len(parsed.work_units) == WORK_UNIT_COUNT


def test_parse_work_units_accepts_valid_parallel_plan() -> None:
    artifact = {
        "work_units": [
            {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
            {
                "unit_id": "u2",
                "description": "B",
                "allowed_directories": ["tests"],
                "dependencies": ["u1"],
            },
        ]
    }

    parsed = parse_work_units_from_artifact(artifact)

    assert parsed is not None
    assert len(parsed.work_units) == WORK_UNIT_COUNT
    assert parsed.work_units[1].dependencies == ["u1"]
