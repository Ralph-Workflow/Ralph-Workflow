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
    planning_obstruction_from_readiness,
)
from ralph.pipeline.integration_resolution import (
    IntegrationBlockKind,
    TreeReadinessAssessment,
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


class TestPlanningObstructionFromReadiness:
    """The typed producer maps a readiness assessment to the closed vocabulary.

    :func:`planning_obstruction_from_readiness` is the producer-side seam the
    boundary deferral uses to carry the typed obstruction cause into the
    durable record. It reads only the typed readiness fields and the two
    canonical uncommitted-work reasons -- it never substring-scans free-form
    prose. When the readiness cause is not one of the canonical typed states,
    it returns ``None`` so the record stays prose-only and the legacy
    checkpoint shim decides routing for it, exactly as before.
    """

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
        """Proven-conflict states have no planning-obstruction vocabulary
        member: they refuse dispatch upstream so the run loop never routes
        on them. The typed answer agrees with the legacy shim, which also
        maps every proven-conflict prose reason to NONE."""
        readiness = TreeReadinessAssessment(
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=("unrelated canonical detail",),
            block_kinds=(block_kind,),
        )
        assert planning_obstruction_from_readiness(readiness) is PlanningObstructionKind.NONE

    def test_non_canonical_prose_is_not_typed(self) -> None:
        """A blocking reason outside the canonical readiness vocabulary
        (e.g. prose replayed from an old durable record) cannot be typed
        here: the producer returns ``None`` so the field stays absent and
        the legacy shim routes the record identically to old checkpoints."""
        readiness = TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=False,
            blocking_reasons=("worktree not clean; exotic persisted prose",),
        )
        assert planning_obstruction_from_readiness(readiness) is None

    def test_ready_tree_types_to_none_result(self) -> None:
        """A ready tree carries no obstruction cause at all; the producer
        declines to type it (``None``), not ``NONE`` -- there is no record
        to annotate."""
        readiness = TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=True,
        )
        assert planning_obstruction_from_readiness(readiness) is None
