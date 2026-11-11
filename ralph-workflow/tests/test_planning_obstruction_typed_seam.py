from __future__ import annotations

import pytest

from ralph.pipeline._planning_obstruction import (
    PlanningObstructionKind,
    identify_planning_obstruction,
    planning_obstruction_from_readiness,
)
from ralph.pipeline.integration_resolution import (
    IntegrationBlockKind,
    TreeReadinessAssessment,
)
from ralph.pipeline.rebase_state import RebaseState


class TestPlanningObstructionTypedSeam:
    @pytest.mark.parametrize(
        "prose",
        [
            "staged uncommitted work blocks integration",
            "STAGED UNCOMMITTED WORK BLOCKS INTEGRATION",
            "fatal: staged uncommitted work blocks integration: foo bar",
            "  staged uncommitted work blocks integration  ",
            "prelude: staged uncommitted work blocks integration; followup: bar",
        ],
    )
    def test_staged_blocks_prose_variants_route_to_staged(self, prose: str) -> None:
        assert (
            identify_planning_obstruction(prose)
            is PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION
        )

    @pytest.mark.parametrize(
        "prose",
        [
            "local changes would be overwritten by merge",
            "local changes to the following files would be overwritten",
            "fatal: local changes to the following files would be overwritten; please commit",
        ],
    )
    def test_local_overwrite_prose_variants_route_to_local(self, prose: str) -> None:
        assert identify_planning_obstruction(prose) is PlanningObstructionKind.LOCAL_OVERWRITE

    @pytest.mark.parametrize(
        "prose",
        [
            "untracked files would be overwritten by checkout",
            "untracked working tree files would be overwritten",
        ],
    )
    def test_untracked_overwrite_prose_variants_route_to_untracked(self, prose: str) -> None:
        assert identify_planning_obstruction(prose) is PlanningObstructionKind.UNTRACKED_OVERWRITE

    @pytest.mark.parametrize(
        "prose",
        [
            "",
            "unrelated prose",
            "the operation succeeded",
            "the agent failed before any planning",
        ],
    )
    def test_unrelated_prose_routes_to_none(self, prose: str) -> None:
        assert identify_planning_obstruction(prose) is PlanningObstructionKind.NONE

    def test_legacy_checkpoint_without_field_routes_to_none(self) -> None:
        state = RebaseState()
        assert state.last_planning_obstruction is None
        kind_str = state.last_planning_obstruction
        routed_kind = (
            PlanningObstructionKind(kind_str) if kind_str else PlanningObstructionKind.NONE
        )
        assert routed_kind is PlanningObstructionKind.NONE

    def test_field_round_trips_through_model_copy(self) -> None:
        original = RebaseState()
        carried = original.model_copy(
            update={
                "last_planning_obstruction": PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION.value
            }
        )
        assert (
            carried.last_planning_obstruction
            == PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION.value
        )
        kind_str = carried.last_planning_obstruction
        routed_kind = (
            PlanningObstructionKind(kind_str) if kind_str else PlanningObstructionKind.NONE
        )
        assert routed_kind is PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION

    def test_unrecognised_field_value_clamps_to_none(self) -> None:
        state = RebaseState().model_copy(update={"last_planning_obstruction": "not_a_real_kind"})
        kind_str = state.last_planning_obstruction
        try:
            routed_kind = PlanningObstructionKind(kind_str)
        except ValueError:
            routed_kind = PlanningObstructionKind.NONE
        assert routed_kind is PlanningObstructionKind.NONE


class TestPlanningObstructionFromReadiness:
    def test_staged_canonical_reason_types_to_staged(self) -> None:
        readiness = TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=False,
            blocking_reasons=("staged uncommitted work blocks integration",),
        )
        assert (
            planning_obstruction_from_readiness(readiness)
            is PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION
        )

    def test_tracked_changes_canonical_reason_types_to_tracked(self) -> None:
        readiness = TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=False,
            blocking_reasons=(
                "worktree not clean; uncommitted tracked changes deferred catch-up integration",
            ),
        )
        assert (
            planning_obstruction_from_readiness(readiness)
            is PlanningObstructionKind.TRACKED_CHANGES_BLOCKS_INTEGRATION
        )

    @pytest.mark.parametrize(
        "block_kind",
        [
            IntegrationBlockKind.UNMERGED_PATHS,
            IntegrationBlockKind.REBASE_IN_PROGRESS,
            IntegrationBlockKind.MERGE_IN_PROGRESS,
        ],
    )
    def test_proven_conflict_block_kinds_type_to_none(
        self, block_kind: IntegrationBlockKind
    ) -> None:
        readiness = TreeReadinessAssessment(
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=("unrelated canonical detail",),
            block_kinds=(block_kind,),
        )
        assert planning_obstruction_from_readiness(readiness) is PlanningObstructionKind.NONE

    def test_non_canonical_prose_is_not_typed(self) -> None:
        readiness = TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=False,
            blocking_reasons=("worktree not clean; exotic persisted prose",),
        )
        assert planning_obstruction_from_readiness(readiness) is None

    def test_ready_tree_types_to_none_result(self) -> None:
        readiness = TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=True,
        )
        assert planning_obstruction_from_readiness(readiness) is None
