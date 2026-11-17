from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.config.models import AgentConfig
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

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.display.parallel_display import ParallelDisplay

_DEFAULTS = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_pass_with_every_resolver_cooling_down_waits_without_working_or_charging_the_cap": (
        "unresolved-integration cooldown must sleep through apply_all_agents_unavailable_wait "
        "+ handle_cooldown_delay at the ledger wait, not a 30s-capped resolver-only fork"
    ),
    "test_pass_with_a_selectable_resolver_runs_the_recovery_preamble_and_is_charged": (
        "a selectable resolver still runs the recovery preamble and charges the conflict counter"
    ),
    "test_pass_that_needs_no_resolver_still_runs_recovery_while_every_resolver_cools_down": (
        "publication-deferred recovery still runs the preamble even when resolvers are cooling"
    ),
}


class _InstalledRegistry:
    def __init__(self, names: Iterable[str]) -> None:
        self._agents: dict[str, AgentConfig] = {name: AgentConfig(cmd=name) for name in names}

    def get(self, name: str) -> AgentConfig | None:
        return self._agents.get(name)


def _noop_emit(_unit_id: object, _line: object) -> None:
    return None


def _ctx(tmp_path: Path, sleeps: list[float], clock: FakeClock) -> run_loop._LoopContext:
    bundle = load_policy(_DEFAULTS)
    ctx = _create_retry_ctx(tmp_path, sleeps)
    ctx.policy_bundle = bundle
    ctx.registry = _InstalledRegistry(resolution_chain_agents(bundle))
    ctx.controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))
    ctx.active_display = cast("ParallelDisplay", SimpleNamespace(emit=_noop_emit))
    ctx.last_waiting_state_phase = None
    ctx.last_waiting_state_sig = None
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
        for _ in range(3):
            bench_resolver(ctx.controller, agent, "kaboom")
    preamble_calls: list[object] = []
    apply_waits: list[int] = []
    handle_delays: list[int] = []

    def preamble(*_args: object, **_kwargs: object) -> RebaseState | None:
        preamble_calls.append(_args)
        return None

    real_apply = ctx.controller.apply_all_agents_unavailable_wait
    real_handle = run_loop.handle_cooldown_delay

    def _spy_apply(
        state: PipelineState,
        phase: str,
        *,
        wait_ms: int,
        unavailability_reason: object = None,
    ) -> PipelineState:
        apply_waits.append(wait_ms)
        return real_apply(
            state,
            phase,
            wait_ms=wait_ms,
            unavailability_reason=unavailability_reason,  # type: ignore[arg-type]
        )

    def _spy_handle(state: PipelineState, loop_ctx: object) -> PipelineState:
        delay = state.last_retry_delay_ms
        if isinstance(delay, int) and delay > 0:
            handle_delays.append(delay)
        return real_handle(state, cast("run_loop.LoopContext", loop_ctx))

    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", preamble)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    monkeypatch.setattr(ctx.controller, "apply_all_agents_unavailable_wait", _spy_apply)
    monkeypatch.setattr(run_loop, "handle_cooldown_delay", _spy_handle)
    monkeypatch.setattr(run_loop, "_handle_cooldown_delay", _spy_handle)
    state = _state()
    expected_wait_ms = ctx.controller.earliest_available_wait_ms(
        "rebase_conflict_resolution",
        list(resolution_chain_agents(ctx.policy_bundle)),
    )

    for _ in range(25):
        state = run_loop._handle_pending_retry(
            state, ctx, state.rebase, "detail", backend=DEFAULT_FILE_BACKEND
        )

    assert preamble_calls == []
    assert state.rebase.consecutive_conflicts == 0
    assert not state.rebase.is_intervention_required
    assert not hasattr(run_loop, "_resolver_cooldown_wait_ms")
    assert apply_waits == [expected_wait_ms] * 25
    assert handle_delays == [expected_wait_ms] * 25
    assert sleeps == [expected_wait_ms / 1000.0] * 25


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
        for _ in range(3):
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
