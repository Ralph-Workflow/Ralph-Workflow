"""While every resolver is cooling down the retry loop waits; it neither works nor gives up."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pipeline import run_loop
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pipeline.conflict_resolution.resolver_health import bench_resolver
from ralph.pipeline.conflict_resolution.session import resolution_chain_agents
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests.test_run_loop_retry_diagnostic import _create_retry_ctx, _noop_save

_DEFAULTS = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_MAX_SLEEP_SLICE_SECONDS = 30.0


def _ctx(tmp_path: Path, sleeps: list[float], clock: FakeClock) -> run_loop._LoopContext:
    bundle = load_policy(_DEFAULTS)
    ctx = _create_retry_ctx(tmp_path, sleeps)
    namespace = cast("SimpleNamespace", ctx)
    namespace.policy_bundle = bundle
    namespace.registry = {agent: object() for agent in resolution_chain_agents(bundle)}
    namespace.controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))
    return ctx


def _state() -> PipelineState:
    return PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_outcome=RebaseOutcomeKind.CONFLICT,
            last_reason="synchronization unproven",
            consecutive_conflicts=0,
        ),
    )


def test_pass_with_every_resolver_cooling_down_waits_without_working_or_charging_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    ctx = _ctx(tmp_path, sleeps, FakeClock())
    for agent in resolution_chain_agents(ctx.policy_bundle):
        bench_resolver(ctx.controller, agent, "kaboom")
    preamble_calls: list[object] = []

    def preamble(*_args: object, **_kwargs: object) -> RebaseState | None:
        preamble_calls.append(_args)
        return None

    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", preamble)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    state = _state()

    for _ in range(25):
        state = run_loop._handle_pending_retry(
            state, ctx, state.rebase, "detail", backend=DEFAULT_FILE_BACKEND
        )

    assert preamble_calls == []
    assert state.rebase.consecutive_conflicts == 0
    assert not state.rebase.is_intervention_required
    assert len(sleeps) == 25
    assert all(0.0 < pause <= _MAX_SLEEP_SLICE_SECONDS for pause in sleeps)


def test_pass_with_a_selectable_resolver_runs_the_recovery_preamble_and_is_charged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    ctx = _ctx(tmp_path, sleeps, FakeClock())
    preamble_calls: list[object] = []

    def preamble(*_args: object, **_kwargs: object) -> RebaseState | None:
        preamble_calls.append(_args)
        return None

    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", preamble)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    state = _state()

    state = run_loop._handle_pending_retry(
        state, ctx, state.rebase, "detail", backend=DEFAULT_FILE_BACKEND
    )

    assert len(preamble_calls) == 1
    assert state.rebase.consecutive_conflicts == 1


def test_pass_that_needs_no_resolver_still_runs_recovery_while_every_resolver_cools_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    ctx = _ctx(tmp_path, sleeps, FakeClock())
    for agent in resolution_chain_agents(ctx.policy_bundle):
        bench_resolver(ctx.controller, agent, "kaboom")
    preamble_calls: list[object] = []

    def preamble(*_args: object, **_kwargs: object) -> RebaseState | None:
        preamble_calls.append(_args)
        return RebaseState(
            last_outcome=RebaseOutcomeKind.COMPLETED,
            last_publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        )

    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", preamble)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_outcome=RebaseOutcomeKind.COMPLETED,
            last_publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        ),
    )

    run_loop._handle_pending_retry(state, ctx, state.rebase, "detail", backend=DEFAULT_FILE_BACKEND)

    assert len(preamble_calls) == 1
