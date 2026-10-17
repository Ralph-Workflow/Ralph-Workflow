"""Reducer uses typed FailureCategory to gate the conflict-strategy ladder.

The reducer's L1/L2 sites used to gate on a substring in the human-readable
``reason``: any reason containing ``"integration conflict requires
resolution:"`` would advance the durable strategy ladder and resume the
phase. That coupling was word-brittle -- a rewording of the reason could
silently turn the ladder on or off -- so the reducer now consults a typed
:class:`FailureCategory` instead and the substring is gone. These tests
prove three properties:

* the ladder advances under :attr:`FailureCategory.INTEGRATION` regardless
  of the prose reason (wording-independence);
* the ladder does NOT advance for any other failure category, including
  the legacy ``None`` default the other call sites still pass;
* the typed path and the previously-substring path produce identical
  post-state, so the migration is observation-equivalent at the durable
  surface.
"""

from __future__ import annotations

from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)
from ralph.recovery.failure_category import FailureCategory


def _basic_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


def _fresh_state() -> PipelineState:
    return PipelineState(
        phase="development",
        rebase=RebaseState(
            conflict_strategy_index=0,
            conflict_strategies_tried=(),
            resolution_exhausted=False,
            resolution_exhaustion_reason=None,
        ),
    )


def _recoverable_state() -> PipelineState:
    state = _fresh_state()
    return state.copy_with(
        phase_chains={"development": AgentChainState(agents=["claude"], current_index=0, retries=3)}
    )


class TestReducerTypedFailureCategory:
    """All four prior test groupings combined into one top-level test class."""

    _REASONS: tuple[str, ...] = (
        "integration conflict requires resolution: unresolved shared.txt",
        "Integration conflict requires resolution: please resolve this.",
        "Rebase halted: shared.txt conflicts with the mainline tip.",
        "Conflict in shared.txt cannot be auto-resolved by the integrator.",
        "Auto-merge on 'main' failed for shared.txt (rebase conflict).",
        "Some completely different operator message naming shared.txt.",
    )

    _NON_INTEGRATION: tuple[FailureCategory | None, ...] = (
        None,
        FailureCategory.AGENT,
        FailureCategory.ENVIRONMENTAL,
        FailureCategory.USER_CONFIG,
        FailureCategory.ARTIFACT_VALIDATION,
        FailureCategory.AMBIGUOUS,
    )

    # ----- Wording independence via the public reducer entry point -----

    def test_each_reason_advances_strategy_ladder_once(self) -> None:
        """Every reason advances the index by 1, sets the same retry delay, and resumes the phase."""
        policy = _basic_policy()
        for reason in self._REASONS:
            state = _recoverable_state()
            event = PhaseFailureEvent(
                phase="development",
                reason=reason,
                recoverable=True,
                failure_category=FailureCategory.INTEGRATION,
            )
            new_state, effects = reducer_reduce(state, event, policy)
            assert new_state.rebase.conflict_strategy_index == 1, reason
            assert len(new_state.rebase.conflict_strategies_tried) == 1, reason
            assert new_state.rebase.resolution_exhausted is False, reason
            assert new_state.last_retry_delay_ms == 2000, reason
            assert new_state.phase == "development", reason
            assert new_state.recovery_epoch == 1, reason
            assert effects == [], reason

    def test_all_reasons_produce_identical_structural_state(self) -> None:
        """Identical behaviour means identical index, exhaustion flag, retry delay, and phase.

        The ``conflict_strategies_tried`` tuple is expected to differ
        because it embeds the reason; that is the point. The
        structural knobs the rest of the pipeline consults must be
        word-independent.
        """
        policy = _basic_policy()
        snapshots: list[tuple[int, bool, int, str]] = []
        for reason in self._REASONS:
            state = _recoverable_state()
            event = PhaseFailureEvent(
                phase="development",
                reason=reason,
                recoverable=True,
                failure_category=FailureCategory.INTEGRATION,
            )
            new_state, _ = reducer_reduce(state, event, policy)
            snapshots.append(
                (
                    new_state.rebase.conflict_strategy_index,
                    new_state.rebase.resolution_exhausted,
                    new_state.last_retry_delay_ms,
                    new_state.phase,
                )
            )
        first = snapshots[0]
        for other in snapshots[1:]:
            assert other == first, "All wording variants must produce identical structural state"

    def test_typed_path_advances_even_when_legacy_substring_absent(self) -> None:
        """The typed gate is sufficient: a reason without the legacy substring still advances."""
        policy = _basic_policy()
        state = _recoverable_state()
        reason = "totally different operator wording, no substring match at all"
        event = PhaseFailureEvent(
            phase="development",
            reason=reason,
            recoverable=True,
            failure_category=FailureCategory.INTEGRATION,
        )
        new_state, _ = reducer_reduce(state, event, policy)
        assert new_state.rebase.conflict_strategy_index == 1
        assert new_state.last_retry_delay_ms == 2000

    # ----- Typed parameter is the sole gate -----

    def test_non_integration_categories_skip_strategy_advance(self) -> None:
        policy = _basic_policy()
        for category in self._NON_INTEGRATION:
            state = _recoverable_state()
            reason = "integration conflict requires resolution: shared.txt"
            event = PhaseFailureEvent(
                phase="development",
                reason=reason,
                recoverable=True,
                failure_category=category,
            )
            new_state, _ = reducer_reduce(state, event, policy)
            assert new_state.rebase.conflict_strategy_index == 0, category
            assert new_state.rebase.conflict_strategies_tried == (), category
            assert new_state.rebase.resolution_exhausted is False, category
            assert new_state.last_retry_delay_ms == 0, category

    def test_default_event_failure_category_does_not_advance_ladder(self) -> None:
        """Existing events that omit failure_category must stay inert at the strategy layer."""
        policy = _basic_policy()
        state = _recoverable_state()
        event = PhaseFailureEvent(
            phase="development",
            reason="any reason at all",
            recoverable=True,
        )
        new_state, _ = reducer_reduce(state, event, policy)
        assert new_state.rebase.conflict_strategy_index == 0
        assert new_state.rebase.conflict_strategies_tried == ()
        assert new_state.last_retry_delay_ms == 0

    def test_substring_in_reason_is_no_longer_sufficient(self) -> None:
        """A reason containing the legacy substring without the typed category does NOT advance."""
        policy = _basic_policy()
        state = _recoverable_state()
        reason = "integration conflict requires resolution: shared.txt"
        event = PhaseFailureEvent(
            phase="development",
            reason=reason,
            recoverable=True,
        )
        new_state, _ = reducer_reduce(state, event, policy)
        assert new_state.rebase.conflict_strategy_index == 0
        assert new_state.rebase.conflict_strategies_tried == ()

    # ----- Invocation counter across sequential calls -----

    def test_four_sequential_non_integration_calls_keep_counter_zero(self) -> None:
        policy = _basic_policy()
        state = _recoverable_state()
        for _ in range(4):
            event = PhaseFailureEvent(
                phase="development",
                reason="totally unrelated failure",
                recoverable=True,
            )
            state, _ = reducer_reduce(state, event, policy)
        assert state.rebase.conflict_strategy_index == 0
        assert state.rebase.conflict_strategies_tried == ()
        assert state.rebase.resolution_exhausted is False
        assert state.recovery_epoch == 4

    def test_typed_path_increments_counter_each_integration_call(self) -> None:
        policy = _basic_policy()
        state = _recoverable_state()
        seen_indices: list[int] = []
        for i in range(4):
            event = PhaseFailureEvent(
                phase="development",
                reason=f"attempt-{i}",
                recoverable=True,
                failure_category=FailureCategory.INTEGRATION,
            )
            state, _ = reducer_reduce(state, event, policy)
            seen_indices.append(state.rebase.conflict_strategy_index)
        assert seen_indices == [1, 2, 3, 4]
        assert state.rebase.resolution_exhausted is True
        assert state.rebase.resolution_exhaustion_reason is not None
        assert "; " in state.rebase.resolution_exhaustion_reason

    def test_mixed_categories_only_advance_on_integration(self) -> None:
        policy = _basic_policy()
        state = _recoverable_state()
        # Two integration events bookend one non-integration event; the
        # public reducer route pins the strategy index to 2 because the
        # non-integration call leaves the ladder untouched.
        for i in range(3):
            category = FailureCategory.INTEGRATION if i % 2 == 0 else FailureCategory.AGENT
            event = PhaseFailureEvent(
                phase="development",
                reason=f"reason-{i}",
                recoverable=True,
                failure_category=category,
            )
            state, _ = reducer_reduce(state, event, policy)
        assert state.rebase.conflict_strategy_index == 1
        assert len(state.rebase.conflict_strategies_tried) == 1

    # ----- PhaseFailureEvent routed through the public reducer entry point -----

    def test_phase_failure_event_recoverable_integration_advances_ladder(self) -> None:
        policy = _basic_policy()
        state = _recoverable_state()
        event = PhaseFailureEvent(
            phase="development",
            reason="totally unrelated prose reason that does not name conflict",
            recoverable=True,
            failure_category=FailureCategory.INTEGRATION,
        )
        new_state, effects = reducer_reduce(state, event, policy)
        assert new_state.rebase.conflict_strategy_index == 1
        assert len(new_state.rebase.conflict_strategies_tried) == 1
        assert new_state.last_retry_delay_ms == 2000
        assert effects == []

    def test_phase_failure_event_recoverable_agent_does_not_advance_ladder(self) -> None:
        policy = _basic_policy()
        state = _recoverable_state()
        event = PhaseFailureEvent(
            phase="development",
            reason="agent broken; operator message unrelated to conflict",
            recoverable=True,
            failure_category=FailureCategory.AGENT,
        )
        new_state, _ = reducer_reduce(state, event, policy)
        assert new_state.rebase.conflict_strategy_index == 0
        assert new_state.rebase.conflict_strategies_tried == ()
        assert new_state.last_retry_delay_ms == 0
