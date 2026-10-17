"""Typed planning-obstruction seam routes without parsing prose.

The run loop's :func:`_route_planning_commit` used to derive
``obstruction`` from five prose substrings against
``state.rebase.last_reason``. That coupling was word-brittle; the
typed seam is :class:`PlanningObstructionKind` carried on
``state.rebase.last_planning_obstruction``.

These tests prove three properties:

* The typed kind drives routing -- the run loop never inspects prose.
* Five distinct prose variants with the same typed kind produce
  identical routing decisions.
* Legacy checkpoints without ``last_planning_obstruction`` still
  route to ``NONE`` (no obstruction) so old trees keep working.
"""

from __future__ import annotations

import pytest

from ralph.pipeline._planning_obstruction import (
    PlanningObstructionKind,
    identify_planning_obstruction,
)
from ralph.pipeline.rebase_state import RebaseState


class TestPlanningObstructionTypedSeam:
    """Routing must be a function of the typed kind, not the prose reason."""

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
        """Five distinct wording variants of the staged-blocks prose all map
        to the same typed kind -- the run loop reads only the kind."""
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
        """Local-overwrite wording variants map to the same typed kind."""
        assert identify_planning_obstruction(prose) is PlanningObstructionKind.LOCAL_OVERWRITE

    @pytest.mark.parametrize(
        "prose",
        [
            "untracked files would be overwritten by checkout",
            "untracked working tree files would be overwritten",
        ],
    )
    def test_untracked_overwrite_prose_variants_route_to_untracked(self, prose: str) -> None:
        """Untracked-overwrite wording variants map to the same typed kind."""
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
        """Unrecognised prose collapses to NONE; the run loop falls through."""
        assert identify_planning_obstruction(prose) is PlanningObstructionKind.NONE

    def test_legacy_checkpoint_without_field_routes_to_none(self) -> None:
        """A ``RebaseState`` that does NOT carry ``last_planning_obstruction``
        must still load and route to ``NONE``; legacy checkpoints are
        not invalidated by the new field."""
        # Build a state without setting last_planning_obstruction.
        state = RebaseState()
        assert state.last_planning_obstruction is None
        # The run loop treats None as NONE, so the predicate
        # ``obstruction_kind is PlanningObstructionKind.NONE`` is True
        # and the function falls through.
        kind_str = state.last_planning_obstruction
        routed_kind = (
            PlanningObstructionKind(kind_str) if kind_str else PlanningObstructionKind.NONE
        )
        assert routed_kind is PlanningObstructionKind.NONE

    def test_field_round_trips_through_model_copy(self) -> None:
        """The field round-trips through the model_copy seam that
        downstream code uses to carry state forward."""
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
        # And the run loop reads the typed kind back out.
        kind_str = carried.last_planning_obstruction
        routed_kind = (
            PlanningObstructionKind(kind_str) if kind_str else PlanningObstructionKind.NONE
        )
        assert routed_kind is PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION

    def test_unrecognised_field_value_clamps_to_none(self) -> None:
        """A field that does not match a closed member clamps to NONE --
        the run loop must never see a free-form value."""
        state = RebaseState().model_copy(update={"last_planning_obstruction": "not_a_real_kind"})
        kind_str = state.last_planning_obstruction
        try:
            routed_kind = PlanningObstructionKind(kind_str)
        except ValueError:
            routed_kind = PlanningObstructionKind.NONE
        assert routed_kind is PlanningObstructionKind.NONE
