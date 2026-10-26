"""Tests for configured counter-cap enforcement (D1/O1).

Verifies that RebaseState and run_loop enforce configured counter-caps
(testing caps 2, 64, 65, and over-cap persisted states) without sleeping
after the cap is reached.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.recovery.controller import RecoveryController
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _noop_warn(_display: object, _msg: str) -> None:
    pass


def _noop_preamble(
    _scope: WorkspaceScope,
    _config: UnifiedConfig,
    *,
    backend: object = None,
) -> RebaseState | None:
    return None


def _create_retry_ctx(
    root: Path,
    sleep_sink: list[float],
) -> run_loop._LoopContext:
    """Build a minimal typed _LoopContext for _wait_for_integration_retry."""
    latest_state: list[PipelineState] = []
    return cast(
        "run_loop._LoopContext",
        SimpleNamespace(
            policy_bundle=SimpleNamespace(
                agents=SimpleNamespace(agent_drains={}, agent_chains={}),
                pipeline=PipelinePolicy(
                    entry_phase="development",
                    phases={
                        "development": PhaseDefinition(
                            drain="development",
                            transitions=PhaseTransition(on_success="complete"),
                        )
                    },
                ),
            ),
            active_display=SimpleNamespace(emit_warn_line=_noop_warn),
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(root),
            latest_state=latest_state,
            sleep=sleep_sink.append,
            connectivity_monitor=SimpleNamespace(current_state=None),
            _session_handoff_dispatched=False,
            registry={},
            controller=RecoveryController(),
        ),
    )


@pytest.mark.parametrize("cap", [2, 64, 65])
def test_rebase_state_record_consecutive_conflicts_respects_cap(cap: int) -> None:
    """RebaseState.record_consecutive_conflicts respects the explicit cap."""
    base = RebaseState()
    # Below cap: passes through
    assert base.record_consecutive_conflicts(cap - 1, cap=cap).consecutive_conflicts == cap - 1
    # At cap: passes through
    assert base.record_consecutive_conflicts(cap, cap=cap).consecutive_conflicts == cap
    # Above cap: clamped to cap
    assert base.record_consecutive_conflicts(cap + 1, cap=cap).consecutive_conflicts == cap
    assert base.record_consecutive_conflicts(cap + 100, cap=cap).consecutive_conflicts == cap
    # Negative and None
    assert base.record_consecutive_conflicts(-5, cap=cap).consecutive_conflicts == 0
    assert base.record_consecutive_conflicts(None, cap=cap).consecutive_conflicts == 0


@pytest.mark.parametrize("cap", [2, 64, 65])
def test_wait_for_retry_pending_path_configurable_cap_escalates_without_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    cap: int,
) -> None:
    """Pending path does not escalate to intervention on conflict count; remains pending and sleeps."""
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    ctx.config = UnifiedConfig.model_validate(
        {"general": {"agent_max_consecutive_unproductive": cap}}
    )
    saved_checkpoints: list[PipelineState] = []

    def _recording_save(st: PipelineState, _c: run_loop._LoopContext) -> None:
        saved_checkpoints.append(st)

    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _recording_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

    handoffs: list[str] = []

    def _recording_handoff(_c: object, failure: str) -> None:
        handoffs.append(failure)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _recording_handoff)

    # Start just below cap: cap - 1 attempts already spent
    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="configurable cap pending detail",
            consecutive_conflicts=cap - 1,
        ),
    )
    assert initial_state.rebase.is_intervention_required is False

    # Pending path remains pending and sleeps, preserving state without intervention
    result = run_loop._wait_for_integration_retry(initial_state, ctx)

    assert sleeps == [5.0], "Pending path must sleep 5s to retry"
    assert len(handoffs) == 0, "Must not hand off when resolution is not exhausted"
    assert result.rebase.is_intervention_required is False
    assert saved_checkpoints[-1].rebase.is_intervention_required is False


@pytest.mark.parametrize("cap", [2, 64, 65])
def test_wait_for_retry_intervention_path_configurable_cap_escalates_without_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    cap: int,
) -> None:
    """Intervention path dispatches handoff once without sleep."""
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    ctx.config = UnifiedConfig.model_validate(
        {"general": {"agent_max_consecutive_unproductive": cap}}
    )
    saved_checkpoints: list[PipelineState] = []

    def _recording_save(st: PipelineState, _c: run_loop._LoopContext) -> None:
        saved_checkpoints.append(st)

    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _recording_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

    handoffs: list[str] = []

    def _recording_handoff(_c: object, failure: str) -> None:
        handoffs.append(failure)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _recording_handoff)

    # Start at cap - 1 with the durable ladder-escalation flag set
    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="configurable cap intervention detail",
            consecutive_conflicts=cap - 1,
        ).set_ladder_escalation("configurable cap intervention detail"),
    )

    result = run_loop._wait_for_integration_retry(initial_state, ctx)

    assert sleeps == [], f"Escalation path must not sleep (cap={cap})"
    assert len(handoffs) == 1
    assert result.rebase.is_intervention_required is True
    assert result.rebase.consecutive_conflicts > 0
    assert saved_checkpoints[-1].rebase.consecutive_conflicts > 0


@pytest.mark.parametrize(
    ("cap", "persisted_count"),
    [
        (2, 5),
        (64, 70),
        (65, 100),
    ],
)
def test_wait_for_retry_over_cap_persisted_pending_governed_by_effective_cap(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    cap: int,
    persisted_count: int,
) -> None:
    """Over-cap persisted state in pending path remains pending and sleeps without handoff."""
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    ctx.config = UnifiedConfig.model_validate(
        {"general": {"agent_max_consecutive_unproductive": cap}}
    )
    saved_checkpoints: list[PipelineState] = []

    def _recording_save(st: PipelineState, _c: run_loop._LoopContext) -> None:
        saved_checkpoints.append(st)

    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _recording_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

    handoffs: list[str] = []

    def _recording_handoff(_c: object, failure: str) -> None:
        handoffs.append(failure)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _recording_handoff)

    # Persisted state loaded with over-cap count, pending (not yet marked intervention_required)
    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="over-cap pending detail",
            consecutive_conflicts=persisted_count,
        ),
    )
    assert initial_state.rebase.consecutive_conflicts > cap

    result = run_loop._wait_for_integration_retry(initial_state, ctx)

    assert sleeps == [5.0], "Pending state sleeps 5s to retry"
    assert len(handoffs) == 0, "No handoff when resolution is not exhausted"
    assert result.rebase.is_intervention_required is False
    assert saved_checkpoints[-1].rebase.is_intervention_required is False


@pytest.mark.parametrize(
    ("cap", "persisted_count"),
    [
        (2, 5),
        (64, 70),
        (65, 100),
    ],
)
def test_wait_for_retry_over_cap_persisted_intervention_governed_by_effective_cap(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    cap: int,
    persisted_count: int,
) -> None:
    """Over-cap persisted state in intervention path dispatches handoff once without sleep."""
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    ctx.config = UnifiedConfig.model_validate(
        {"general": {"agent_max_consecutive_unproductive": cap}}
    )
    saved_checkpoints: list[PipelineState] = []

    def _recording_save(st: PipelineState, _c: run_loop._LoopContext) -> None:
        saved_checkpoints.append(st)

    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _recording_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

    handoffs: list[str] = []

    def _recording_handoff(_c: object, failure: str) -> None:
        handoffs.append(failure)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _recording_handoff)

    # Persisted state loaded with over-cap count, already marked ladder-escalation
    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="over-cap intervention detail",
            consecutive_conflicts=persisted_count,
        ).set_ladder_escalation("over-cap intervention detail"),
    )
    assert initial_state.rebase.consecutive_conflicts > cap

    result = run_loop._wait_for_integration_retry(initial_state, ctx)

    assert sleeps == [], (
        f"Over-cap escalation must not sleep (cap={cap}, persisted={persisted_count})"
    )
    assert len(handoffs) == 1
    assert result.rebase.is_intervention_required is True
    assert result.rebase.consecutive_conflicts > 0
    assert saved_checkpoints[-1].rebase.consecutive_conflicts > 0
