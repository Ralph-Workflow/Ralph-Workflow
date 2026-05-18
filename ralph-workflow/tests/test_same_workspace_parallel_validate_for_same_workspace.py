"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.work_units import (
    WorkUnit,
    WorkUnitsPlan,
    WorkUnitsValidationError,
    validate_for_same_workspace,
)

if TYPE_CHECKING:
    from ralph.mcp.multimodal.capabilities import (
        MultimodalModelIdentity,
        ResolvedCapabilityProfile,
    )


def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


@dataclass
class _SessionContract:
    """Bundled session contract parameters to reduce argument count."""

    drain: str = ""
    capabilities: frozenset[str] = frozenset()
    model_identity: MultimodalModelIdentity | None = None
    capability_profile: ResolvedCapabilityProfile | None = None


class TestValidateForSameWorkspace:
    def test_two_safe_disjoint_workers_passes(self) -> None:
        plan = WorkUnitsPlan(
            work_units=[
                _make_unit("a", ["src/api"]),
                _make_unit("b", ["src/frontend"]),
            ]
        )
        validate_for_same_workspace(plan)  # should not raise

    def test_overlapping_directories_rejected(self) -> None:
        plan = WorkUnitsPlan(
            work_units=[
                _make_unit("a", ["src/api"]),
                _make_unit("b", ["src/api/auth"]),
            ]
        )
        with pytest.raises(WorkUnitsValidationError, match="overlaps"):
            validate_for_same_workspace(plan)

    def test_missing_allowed_directories_rejected(self) -> None:
        plan = WorkUnitsPlan(
            work_units=[
                WorkUnit(unit_id="a", description="missing dirs", allowed_directories=[]),
            ]
        )
        with pytest.raises(
            WorkUnitsValidationError, match="does not declare any allowed_directories"
        ):
            validate_for_same_workspace(plan)

    def test_reserved_path_dot_agent_rejected(self) -> None:
        plan = WorkUnitsPlan(
            work_units=[
                _make_unit("a", [".agent/custom"]),
            ]
        )
        with pytest.raises(WorkUnitsValidationError, match="reserved path"):
            validate_for_same_workspace(plan)

    def test_reserved_path_dot_git_rejected(self) -> None:
        plan = WorkUnitsPlan(
            work_units=[
                _make_unit("a", [".git/hooks"]),
            ]
        )
        with pytest.raises(WorkUnitsValidationError, match="reserved path"):
            validate_for_same_workspace(plan)

    def test_no_prefix_overlap_different_second_segment(self) -> None:
        plan = WorkUnitsPlan(
            work_units=[
                _make_unit("a", ["src/api"]),
                _make_unit("b", ["src/api2"]),
            ]
        )
        validate_for_same_workspace(plan)  # should not raise

    def test_exact_match_overlap_rejected(self) -> None:
        plan = WorkUnitsPlan(
            work_units=[
                _make_unit("a", ["src/shared"]),
                _make_unit("b", ["src/shared"]),
            ]
        )
        with pytest.raises(WorkUnitsValidationError, match="overlaps"):
            validate_for_same_workspace(plan)
