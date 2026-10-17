"""End-to-end regression coverage for exhausted integration strategies."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, RecoveryPolicy
from ralph.recovery.classifier import FailureCategory
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

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
    """Every failed rung advances; exhaustion escalates to the typed
    ``INTERVENTION_REQUIRED`` transition through the
    :meth:`RebaseState.set_intervention_required` single-writer
    setter.

    The previous behaviour of this test asserted that the run loop
    sleeps, retries, and eventually dispatches ordinary work. That
    pattern violated bounded-progress: it silently replenished the
    attempt budget across crashes and hid a real exhaustion. The
    architecture now forbids it, so the test exercises the helper
    directly to prove the typed-setter contract.
    """
    reason = "integration conflict requires resolution: unresolved shared.txt"
    state = PipelineState(phase="development")
    policy = _policy()

    for _ in range(4):
        state, _ = reduce(
            state,
            PhaseFailureEvent(
                phase="development",
                reason=reason,
                recoverable=False,
                failure_category=FailureCategory.INTEGRATION,
            ),
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

    # The exhausted state is escalated to INTERVENTION_REQUIRED through
    # the typed single-writer setter, WITHOUT a recovery preamble call
    # and WITHOUT a sleep. Exercise the helper directly because the
    # full run loop would loop forever in a tight block (the
    # integration is still unresolved and the exhausted state
    # immediately escalates without waiting).
    preamble_invocations: list[object] = []
    sleeps: list[float] = []

    def _preamble(workspace_scope: object, config: object, *, backend: object) -> object:
        preamble_invocations.append(getattr(workspace_scope, "root", None))
        return None

    workspace = MemoryWorkspace("/memory/recovery")
    workspace.write("main", "before")
    workspace.write("HEAD", "feature")

    def _record_sleep(delay: float) -> None:
        sleeps.append(delay)

    ctx = cast(
        "run_loop._LoopContext",
        SimpleNamespace(
            policy_bundle=SimpleNamespace(pipeline=policy),
            active_display=SimpleNamespace(emit_warn_line=lambda *a, **k: None),
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(workspace.root),
            latest_state=[],
            sleep=_record_sleep,
            connectivity_monitor=SimpleNamespace(current_state=None),
        ),
    )

    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _preamble)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *a, **k: None)

    updated = run_loop._wait_for_integration_retry(state, ctx)

    # The exhausted state escalates to INTERVENTION_REQUIRED. The
    # carried counters and the resolution_exhausted flag survive
    # across the transition -- the run-loop consumer that gates
    # dispatch keeps refusing ordinary work.
    assert updated.rebase.last_outcome is not None
    assert updated.rebase.is_intervention_required is True
    # The conflict-strategy block is preserved: the typed
    # ``INTERVENTION_REQUIRED`` transition does NOT cycle or reset it.
    assert updated.rebase.conflict_strategy_index == state.rebase.conflict_strategy_index
    assert updated.rebase.conflict_strategies_tried == state.rebase.conflict_strategies_tried
    assert updated.rebase.resolution_exhausted is True
    # The recovery preamble is NOT called when the state is
    # exhausted.
    assert preamble_invocations == []
    # The exhausted path does NOT sleep.
    assert sleeps == []
