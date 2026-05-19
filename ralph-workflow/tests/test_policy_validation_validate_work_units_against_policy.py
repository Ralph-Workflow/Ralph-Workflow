"""Unit tests for policy validation.

Tests cover:
- Valid agents.toml loading
- Missing drain binding raises PolicyValidationError
- Chain referencing unknown agent raises PolicyValidationError
- Empty chain list raises PolicyValidationError
- All six built-in drains are bound in default agents.toml
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from ralph.pipeline.work_units import parse_work_units_from_artifact
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    PhaseDefinition,
    PhaseParallelization,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.policy.validation import (
    PolicyValidationError,
    validate_work_units_against_policy,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestValidateWorkUnitsAgainstPolicy:
    """Tests for planning work_units policy validation."""

    def _minimal_pipeline(
        self,
        *,
        parallelization: PhaseParallelization | None = None,
    ) -> PipelinePolicy:
        return PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="complete"),
                    parallelization=parallelization,
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

    def test_multi_work_units_requires_parallel_execution_policy(self) -> None:
        pipeline = self._minimal_pipeline()
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": ["tests"]},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="parallelization"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

    def test_multi_work_units_respects_max_parallel_workers(self) -> None:
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(max_parallel_workers=1)
        )
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": ["tests"]},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="max_parallel_workers"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

    def test_work_units_count_cap_exceeded(self) -> None:
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {
                        "unit_id": f"u{i}",
                        "description": f"Work unit {i}",
                        "allowed_directories": ["src"],
                    }
                    for i in range(51)
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="exceeds cap"):
            validate_work_units_against_policy(work_units, bundle.pipeline, phase="development")

    def test_work_units_count_cap_custom(self) -> None:
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(
                max_parallel_workers=8,
                max_work_units=3,
            )
        )

        allowed_work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {
                        "unit_id": f"u{i}",
                        "description": f"Work unit {i}",
                        "allowed_directories": [f"dir{i}"],
                    }
                    for i in range(3)
                ]
            }
        )
        assert allowed_work_units is not None

        validate_work_units_against_policy(allowed_work_units, pipeline, phase="planning")

        rejected_work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {
                        "unit_id": f"u{i}",
                        "description": f"Work unit {i}",
                        "allowed_directories": [f"dir{i}"],
                    }
                    for i in range(4)
                ]
            }
        )
        assert rejected_work_units is not None

        with pytest.raises(PolicyValidationError, match="exceeds cap"):
            validate_work_units_against_policy(rejected_work_units, pipeline, phase="planning")

    def test_overlapping_edit_areas_raise_policy_validation_error(self) -> None:
        """Work units with overlapping allowed_directories must raise PolicyValidationError."""
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(max_parallel_workers=2)
        )
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": ["src/subdir"]},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="overlaps"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

    def test_missing_allowed_directories_raises_policy_validation_error(self) -> None:
        """Work units without allowed_directories must raise PolicyValidationError."""
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(max_parallel_workers=2)
        )
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B"},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="allowed_directories"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

    def test_disjoint_edit_areas_pass_validation(self) -> None:
        """Work units with disjoint allowed_directories must pass validation."""
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(max_parallel_workers=2)
        )
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": ["tests"]},
                ]
            }
        )
        assert work_units is not None

        validate_work_units_against_policy(work_units, pipeline, phase="planning")  # must not raise

    def test_reserved_path_at_policy_load_raises_policy_validation_error(self) -> None:
        """Work units declaring reserved paths raise PolicyValidationError at policy load time."""
        pipeline = self._minimal_pipeline(
            parallelization=PhaseParallelization(max_parallel_workers=2)
        )
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": [".agent/custom"]},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="reserved path"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")

    def test_validation_does_not_run_for_phase_without_parallelization(self) -> None:
        """A phase with no parallelization rejects multi-work-unit plans fail-closed."""
        pipeline = self._minimal_pipeline()  # planning phase has no parallelization
        work_units = parse_work_units_from_artifact(
            {
                "work_units": [
                    # Overlapping — but the phase-scoped error fires before the overlap check
                    {"unit_id": "u1", "description": "A", "allowed_directories": ["src"]},
                    {"unit_id": "u2", "description": "B", "allowed_directories": ["src/sub"]},
                ]
            }
        )
        assert work_units is not None

        with pytest.raises(PolicyValidationError, match="does not declare parallelization"):
            validate_work_units_against_policy(work_units, pipeline, phase="planning")
