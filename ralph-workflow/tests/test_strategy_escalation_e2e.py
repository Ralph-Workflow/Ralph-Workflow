"""End-to-end regression coverage for exhausted integration strategies."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

from ralph.pipeline import run_loop
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


def test_strategy_escalation_regression_exhausts_once_with_full_history() -> None:
    """S-8: every failed rung advances, then the exhausted run exits once."""
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

    display = MagicMock()
    ctx = cast(
        "run_loop._LoopContext",
        SimpleNamespace(
        policy_bundle=SimpleNamespace(pipeline=policy),
            active_display=display,
        ),
    )
    result, previous_phase, exit_code = run_loop._run_inner_loop_after_startup(
        state, ctx, "development"
    )

    assert exit_code == 1
    assert previous_phase == "development"
    assert result.last_error is not None
    assert state.rebase.resolution_exhaustion_reason is not None
    assert state.rebase.resolution_exhaustion_reason in result.last_error
    display.emit.assert_called_once()
