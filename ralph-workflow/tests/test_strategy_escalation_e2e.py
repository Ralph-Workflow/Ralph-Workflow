from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline._integration_action import RebaseOutcomeKind
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
            _session_handoff_dispatched=False,
        ),
    )

    handoff_calls: list[str] = []

    def _record_handoff(_ctx: object, failure: str, *, backend: object = None) -> None:
        handoff_calls.append(failure)

    monkeypatch.setattr(run_loop, "_dispatch_retained_handoff", _record_handoff)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *a, **k: None)

    updated = run_loop._wait_for_integration_retry(state, ctx)

    assert updated.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert updated.rebase.is_intervention_required is True
    assert updated.rebase.conflict_strategy_index == state.rebase.conflict_strategy_index
    assert updated.rebase.conflict_strategies_tried == state.rebase.conflict_strategies_tried
    assert updated.rebase.resolution_exhausted is True
    assert len(handoff_calls) == 1, (
        "Cold exhausted state must dispatch one session-scoped automated "
        "resolver handoff (O1/O3 cold durable exhausted contract)."
    )
    assert sleeps == []
    handoff_calls.clear()
    run_loop._wait_for_integration_retry(updated, ctx)
    assert handoff_calls == [], (
        "Same-session calls must not re-dispatch the automated resolver handoff."
    )
    assert sleeps == [run_loop._REPEAT_ESCALATION_RETRY_SECONDS]
