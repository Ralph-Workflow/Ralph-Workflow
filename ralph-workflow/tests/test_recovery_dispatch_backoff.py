"""Regression coverage for conflict-strategy recovery backoff."""

from __future__ import annotations

from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, RecoveryPolicy


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete", on_loopback="development"),
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


def test_conflict_strategy_retry_uses_bounded_exponential_backoff() -> None:
    """The next rung cannot immediately hot-loop at the recovery boundary."""
    state, _ = reduce(
        PipelineState(phase="development"),
        PhaseFailureEvent(
            phase="development",
            reason="integration conflict requires resolution: unresolved shared.txt",
            recoverable=False,
        ),
        _policy(),
    )

    assert state.rebase.conflict_strategy_index == 1
    assert state.last_retry_delay_ms == 2000
