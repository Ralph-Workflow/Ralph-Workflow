"""End-to-end regression coverage for exhausted integration strategies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, RecoveryPolicy
from tests.test_run_loop_failed_ladder_terminal import run_recovery_scenario

if TYPE_CHECKING:
    from pytest import MonkeyPatch


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


def test_strategy_escalation_preserves_history_and_resumes_after_recovery(
    monkeypatch: MonkeyPatch,
) -> None:
    """Every failed rung advances; exhaustion still permits delayed recovery."""
    reason = "integration conflict requires resolution: unresolved shared.txt"
    state = PipelineState(phase="development")
    policy = _policy()

    for _ in range(4):
        state, _ = reduce(
            state,
            PhaseFailureEvent(phase="development", reason=reason, recoverable=False),
            policy,
        )

    assert state.rebase.conflict_strategy_index == 4
    assert state.rebase.resolution_exhausted is True
    assert [entry.split(":", 1)[0] for entry in state.rebase.conflict_strategies_tried] == [
        "rebase_resolver",
        "refresh_retry",
        "merge_instead",
        "resolver_with_history",
    ]

    observed = run_recovery_scenario(monkeypatch, state)

    assert observed.exit_code == 0
    assert observed.waits and all(delay > 0 for delay in observed.waits)
    assert [dispatched.phase for dispatched in observed.dispatched] == ["development"]
    assert any("CRITICAL" in warning for warning in observed.warnings)
    assert (
        observed.saved[0].rebase.conflict_strategies_tried == state.rebase.conflict_strategies_tried
    )
    assert observed.result.rebase.integration_unresolved is False
