"""Regression test for priority reselection after cooldown waiting."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import run_loop
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.integration_resolution import (
    RECOVERABLE,
    RESOLVED,
    IntegrationResolutionVerdict,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent")


def _entry(until_ms: int) -> UnavailabilityEntry:
    return UnavailabilityEntry(
        unavailable_until_ms=until_ms,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=5000,
        max_backoff_ms=5000,
    )


def test_run_loop_resumes_on_highest_priority_newly_available_agent(
    monkeypatch: Any,
) -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_entries={
                "development:claude": _entry(5000),
                "development:opencode": _entry(8000),
                "development:agy": _entry(10000),
            },
        )
    )
    policy_bundle = MagicMock()
    policy_bundle.pipeline.terminal_phase = "complete"
    connectivity_monitor = MagicMock()
    connectivity_monitor.current_state = "online"
    ctx = run_loop._LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=MagicMock(),
        config=MagicMock(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=0,
        registry=MagicMock(),
        effective_pipeline_subscriber=None,
        controller=controller,
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=connectivity_monitor,
        sleep=clock.advance,
        is_quiet=False,
        snapshot_registry=None,
        last_waiting_state_phase=None,
    )
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode", "agy"],
                current_index=2,
                retries=2,
            )
        },
    ).copy_with(last_retry_delay_ms=5000, is_waiting_state=True)

    seen_states: list[PipelineState] = []

    def run_step(*, state: PipelineState, **_kwargs: object) -> PipelineState:
        seen_states.append(state)
        if len(seen_states) == 1:
            return state
        return state.copy_with(phase="complete")

    emitted: list[str] = []
    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", run_step)
    monkeypatch.setattr(
        "ralph.pipeline.run_loop.emit_activity_line",
        lambda _display, _phase, text: emitted.append(text),
    )

    logs: list[str] = []

    def sink(msg: Any) -> None:
        logs.append(str(msg))

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        run_loop._run_inner_loop(state, ctx, prev_phase="development")
    finally:
        logger.remove(sink_id)

    assert len(seen_states) == 2
    resumed_chain = seen_states[1].chain_for_phase("development")
    assert resumed_chain is not None
    assert resumed_chain.current_index == 0
    assert resumed_chain.retries == 0
    assert seen_states[1].last_agent_session_id is None
    assert seen_states[1].is_waiting_state is False
    assert any("Phase development: Selected agent claude" in log for log in logs)


def test_cooldown_resume_does_not_reselect_when_integration_is_unresolved(
    monkeypatch: Any,
) -> None:
    """A cooldown cannot prepare an ordinary agent while resolution is pending."""
    ctx = MagicMock()
    ctx.workspace_scope.root = Path("/workspace")
    ctx.active_display = MagicMock()
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["claude", "opencode"], current_index=1)
        },
    ).copy_with(rebase=run_loop.RebaseState(last_action="conflict"), is_waiting_state=True)
    reselections: list[PipelineState] = []
    monkeypatch.setattr(run_loop, "emit_activity_line", lambda *_args: None)
    monkeypatch.setattr(run_loop, "_log_resumed_state", lambda *_args: None)
    monkeypatch.setattr(
        run_loop,
        "_reselect_preferred_agent",
        lambda candidate, _ctx: reselections.append(candidate) or candidate,
    )
    monkeypatch.setattr(
        run_loop,
        "inspect_integration_resolution",
        lambda *_args: IntegrationResolutionVerdict(RECOVERABLE),
    )

    resumed = run_loop._resume_after_cooldown_wait(state, ctx, "development", "offline", 10)

    assert reselections == [], "blocked cooldown resume must not prepare an ordinary dispatch"
    chain = resumed.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert resumed.is_waiting_state is False


@pytest.mark.parametrize("auto_integrate_enabled", (True, False))
def test_recoverable_mid_run_verdict_reenters_resolution_before_dispatch(
    monkeypatch: Any,
    auto_integrate_enabled: bool,
) -> None:
    """A late conflict uses the resolver seam even when auto integration is disabled."""
    config = MagicMock()
    config.general.auto_integrate_enabled = auto_integrate_enabled
    ctx = MagicMock()
    ctx.config = config
    ctx.workspace_scope.root = Path("/workspace")
    state = PipelineState(phase="planning")
    recoverable = IntegrationResolutionVerdict(
        status=RECOVERABLE,
        reasons=("unmerged paths remain from an unfinished rebase or merge: a.py",),
        recovery_executor="rebase_conflict_resolution",
    )
    resolved = IntegrationResolutionVerdict(
        status=RESOLVED,
    )
    inspections = iter((recoverable, resolved))
    startup = MagicMock(return_value=state.rebase)
    monkeypatch.setattr(run_loop, "inspect_integration_resolution", lambda *_args: next(inspections))
    monkeypatch.setattr(run_loop, "_run_startup_integration", startup)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *_args: None)

    assert run_loop._block_unresolved_integration(state, ctx, "analysis") is None
    startup.assert_called_once_with(ctx, state.rebase)


def test_exhausted_mid_run_verdict_terminates_without_resolution_reentry(
    monkeypatch: Any,
) -> None:
    """Only durable resolver exhaustion may stop an otherwise ordinary loop."""
    config = MagicMock()
    config.general.auto_integrate_enabled = True
    ctx = MagicMock()
    ctx.config = config
    ctx.workspace_scope.root = Path("/workspace")
    state = PipelineState(phase="planning")
    exhausted = IntegrationResolutionVerdict(
        status=run_loop.EXHAUSTED,
        reasons=("chain exhausted",),
    )
    startup = MagicMock()
    monkeypatch.setattr(run_loop, "inspect_integration_resolution", lambda *_args: exhausted)
    monkeypatch.setattr(run_loop, "_run_startup_integration", startup)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *_args: None)
    monkeypatch.setattr(run_loop, "_announce_deferred_startup_integration", lambda *_args: None)

    assert run_loop._block_unresolved_integration(state, ctx, "analysis") == (state, "analysis", 1)
    startup.assert_not_called()


def test_recoverable_verdict_always_enters_conflict_resolution(
    monkeypatch: Any,
) -> None:
    """Any recoverable integration evidence must invoke the conflict resolver.

    Regression guard for the deadlock that made ``rdev`` exit with zero agent
    calls: the only recovery executor wired to a recoverable verdict was
    ``_run_startup_integration``, which routes through
    ``auto_integrate_on_phase_transition`` and defers the entire boundary when
    the worktree is dirty. Every conflicted worktree is dirty, so the resolver
    was never invoked for exactly the evidence it exists to clear, and the run
    terminated instead. The resolver must be called directly, and before the
    startup seam.
    """
    config = MagicMock()
    config.general.auto_integrate_enabled = True
    ctx = MagicMock()
    ctx.config = config
    ctx.workspace_scope.root = Path("/workspace")
    state = PipelineState(phase="planning")
    recoverable = IntegrationResolutionVerdict(
        status=RECOVERABLE,
        reasons=("unmerged paths remain from an unfinished rebase or merge: a.py",),
        recovery_executor="rebase_conflict_resolution",
    )
    calls: list[str] = []
    inspections = iter((recoverable, IntegrationResolutionVerdict(status=RESOLVED)))

    def _resolve(_ctx: Any, _rebase: Any = None) -> bool:
        calls.append("conflict_resolution")
        return True

    def _startup(*_args: Any) -> Any:
        calls.append("startup_integration")
        return state.rebase

    monkeypatch.setattr(run_loop, "inspect_integration_resolution", lambda *_a: next(inspections))
    monkeypatch.setattr(run_loop, "_run_integration_conflict_resolution", _resolve)
    monkeypatch.setattr(run_loop, "_run_startup_integration", _startup)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *_a: None)
    monkeypatch.setattr(run_loop, "_announce_conflict_resolution_entry", lambda *_a: None)

    assert run_loop._block_unresolved_integration(state, ctx, "analysis") is None
    assert calls == ["conflict_resolution", "startup_integration"]


def test_conflict_resolution_runs_even_when_startup_integration_defers(
    monkeypatch: Any,
) -> None:
    """The resolver still runs when the startup seam reports nothing to do.

    ``_run_startup_integration`` returns ``None`` on a dirty worktree. That
    must not suppress conflict resolution.
    """
    config = MagicMock()
    config.general.auto_integrate_enabled = True
    ctx = MagicMock()
    ctx.config = config
    ctx.workspace_scope.root = Path("/workspace")
    state = PipelineState(phase="planning")
    recoverable = IntegrationResolutionVerdict(
        status=RECOVERABLE,
        reasons=("rebase is in progress",),
        recovery_executor="rebase_conflict_resolution",
    )
    resolver = MagicMock(return_value=True)
    inspections = iter((recoverable, IntegrationResolutionVerdict(status=RESOLVED)))

    monkeypatch.setattr(run_loop, "inspect_integration_resolution", lambda *_a: next(inspections))
    monkeypatch.setattr(run_loop, "_run_integration_conflict_resolution", resolver)
    monkeypatch.setattr(run_loop, "_run_startup_integration", lambda *_a: None)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *_a: None)
    monkeypatch.setattr(run_loop, "_announce_conflict_resolution_entry", lambda *_a: None)

    assert run_loop._block_unresolved_integration(state, ctx, "analysis") is None
    assert resolver.call_count == 1
    assert resolver.call_args.args[0] is ctx


def test_conflict_resolution_executor_never_raises(monkeypatch: Any) -> None:
    """A failing resolver degrades to False rather than aborting the loop."""
    ctx = MagicMock()
    ctx.workspace_scope.root = Path("/workspace")

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _boom)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target",
        lambda *_a: "main",
    )

    assert run_loop._run_integration_conflict_resolution(ctx) is False


def test_blocked_dispatch_announces_the_real_evidence(monkeypatch: Any) -> None:
    """A terminating block must name the live evidence, not the empty record.

    Regression guard: the terminating seam reused the crash-recovery
    announcement, so a run blocked by live git evidence died printing
    "crash recovery still owns the durable integration record (None)" --
    a message that names an empty record and hides the actual cause. The
    verdict's own reasons and executor must be reported.
    """
    lines: list[str] = []
    ctx = MagicMock()
    monkeypatch.setattr(
        run_loop,
        "emit_integration_warn_line",
        lambda _display, message: lines.append(message),
    )

    run_loop._announce_blocked_dispatch(
        ctx,
        IntegrationResolutionVerdict(
            status=RECOVERABLE,
            reasons=("unmerged paths remain from an unfinished rebase or merge: a.py",),
            recovery_executor="rebase_conflict_resolution",
        ),
    )

    assert len(lines) == 1
    assert "unmerged paths remain" in lines[0]
    assert "rebase_conflict_resolution" in lines[0]
    assert "(None)" not in lines[0]


def test_blocked_dispatch_announcement_never_raises(monkeypatch: Any) -> None:
    """A display that cannot take the line must not abort the run."""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("display gone")

    monkeypatch.setattr(run_loop, "emit_integration_warn_line", _boom)

    run_loop._announce_blocked_dispatch(
        MagicMock(),
        IntegrationResolutionVerdict(status=RECOVERABLE, reasons=(), recovery_executor=None),
    )


def test_direct_resolver_call_is_charged_against_the_conflict_budget(
    monkeypatch: Any,
) -> None:
    """The direct resolver call must not bypass the anti-thrash budget.

    ``auto_integrate_after_commit`` gates resolver spend on
    ``resolver_allowed``. The direct call added for the dirty-worktree
    deadlock fix must honour the same bound, or re-running on an
    unresolvable conflict would pay for an agent invocation every time.
    """
    from ralph.pipeline.auto_integrate_conflict_budget import (
        MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
    )
    from ralph.pipeline.rebase_state import RebaseState

    built: list[str] = []
    ctx = MagicMock()
    ctx.workspace_scope.root = Path("/workspace")
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target", lambda *_a: "main"
    )

    def _build(**_kwargs: Any) -> Any:
        built.append("built")
        return lambda _root, _target: True

    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _build)
    monkeypatch.setattr(run_loop, "_complete_in_progress_merge", lambda *_a: True)

    # Fresh state: the first attempt is always allowed.
    assert run_loop._run_integration_conflict_resolution(ctx, RebaseState()) is True
    assert len(built) == 1

    # Budget spent against the same target: suppressed, resolver never built.
    spent = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
    )
    assert run_loop._run_integration_conflict_resolution(ctx, spent) is False
    assert len(built) == 1


def test_a_paused_rebase_reaches_a_resolver_instead_of_being_dropped(monkeypatch: Any) -> None:
    """A paused rebase is the commoner blocking state, and it is not a merge.

    The seam built the MERGE resolver and dropped it whenever no merge
    was in progress, so a conflicted rebase reached a resolver that was
    standing right there and was never invoked -- the run then exited
    with zero resolution attempts.
    """
    ctx = MagicMock()
    ctx.workspace_scope.root = Path("/workspace")
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target", lambda *_a: "main"
    )
    monkeypatch.setattr(
        run_loop, "build_agent_conflict_resolver", lambda **_k: (lambda _r, _t: True)
    )
    monkeypatch.setattr(run_loop, "_paused_rebase_at", lambda _root: True)
    merge_calls: list[str] = []
    monkeypatch.setattr(
        run_loop,
        "_complete_in_progress_merge",
        lambda *_a: merge_calls.append("merge") is None,
    )
    rebase_calls: list[str] = []
    monkeypatch.setattr(
        run_loop,
        "_resolve_paused_rebase",
        lambda _ctx, target: rebase_calls.append(target) is None or True,
    )

    assert run_loop._run_integration_conflict_resolution(ctx) is True
    assert rebase_calls == ["main"]
    assert merge_calls == []


def test_a_paused_rebase_is_driven_through_the_stop_resolver(monkeypatch: Any) -> None:
    """It must use the rebase-stop resolver, not the two-argument merge one."""
    from ralph.pipeline import auto_integrate_rebase_merge as rebase_merge

    ctx = MagicMock()
    ctx.workspace_scope.root = Path("/workspace")
    built: list[str] = []
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_agent.build_agent_rebase_stop_resolver",
        lambda **_k: built.append("stop-resolver") or (lambda *_a: True),
    )
    monkeypatch.setattr(
        rebase_merge,
        "_resolve_rebase_with_config",
        lambda _root, _target, _resolver, _config: (True, None),
    )

    assert run_loop._resolve_paused_rebase(ctx, "main") is True
    assert built == ["stop-resolver"]


def test_a_recorded_exhaustion_does_not_block_a_different_conflict(monkeypatch: Any) -> None:
    """An exhausted record describes ONE conflict; it must not refuse another.

    Treating `resolution_exhausted` as an unconditional stop ended the
    run without invoking a resolver at all, so a conflict that had since
    changed was refused by evidence recorded about a different one.
    """
    from ralph.pipeline.auto_integrate_conflict_budget import (
        MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
        ConflictIdentity,
    )
    from ralph.pipeline.rebase_state import RebaseState

    ctx = MagicMock()
    ctx.workspace_scope.root = Path("/workspace")
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target", lambda *_a: "main"
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.observe_conflict_identity",
        lambda _root, target, **_kwargs: ConflictIdentity(
            feature_sha="feature-2", target_sha="target-2", scope="feature"
        ),
    )
    spent_on_another_conflict = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
        resolution_exhausted=True,
        resolution_exhaustion_reason="ATTEMPT_FAILED: conflict markers survive in: a.py",
        last_conflict_feature_sha="feature-1",
        last_conflict_target_sha="target-1",
        last_conflict_scope="feature",
    )
    assert run_loop._exhaustion_still_binds(ctx, spent_on_another_conflict) is False

    same_conflict = spent_on_another_conflict.model_copy(
        update={"last_conflict_feature_sha": "feature-2", "last_conflict_target_sha": "target-2"}
    )
    assert run_loop._exhaustion_still_binds(ctx, same_conflict) is True


def test_an_exhausted_record_still_reaches_the_resolver_when_it_does_not_bind(
    monkeypatch: Any,
) -> None:
    """The whole point: a non-binding exhaustion must reach conflict resolution."""
    from ralph.pipeline.integration_resolution import IntegrationResolutionVerdict
    from ralph.pipeline.integration_resolution_types import IntegrationResolutionStatus
    from ralph.pipeline.rebase_state import RebaseState

    ctx = MagicMock()
    ctx.workspace_scope.root = Path("/workspace")
    resolved: list[str] = []
    monkeypatch.setattr(
        run_loop,
        "inspect_integration_resolution",
        lambda _root, _rebase: IntegrationResolutionVerdict(
            status=IntegrationResolutionStatus.EXHAUSTED,
            reasons=("conflict resolver exhausted",),
        ),
    )
    monkeypatch.setattr(run_loop, "_exhaustion_still_binds", lambda _ctx, _rebase: False)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", lambda *_a: None)
    monkeypatch.setattr(run_loop, "_announce_conflict_resolution_entry", lambda *_a: None)
    monkeypatch.setattr(run_loop, "_announce_blocked_dispatch", lambda *_a: None)
    monkeypatch.setattr(run_loop, "_announce_deferred_startup_integration", lambda *_a: None)
    monkeypatch.setattr(
        run_loop,
        "_run_integration_conflict_resolution",
        lambda _ctx, _rebase: resolved.append("resolver ran") is None,
    )
    monkeypatch.setattr(run_loop, "_run_startup_integration", lambda *_a: None)

    state = MagicMock()
    state.rebase = RebaseState(resolution_exhausted=True)
    run_loop._block_unresolved_integration(state, ctx, "planning")
    assert resolved == ["resolver ran"]


def test_a_different_conflict_gets_its_own_resolver_budget(monkeypatch: Any) -> None:
    """The budget is spent on ONE conflict, so a new one must not inherit it.

    Consulting the budget without observing which conflict is on disk
    spends the identity that matches every conflict: a genuinely new
    conflict then inherits an older one's exhausted count and is
    escalated without a resolver ever being invoked.
    """
    from ralph.pipeline.auto_integrate_conflict_budget import (
        MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
        ConflictIdentity,
    )
    from ralph.pipeline.rebase_state import RebaseState

    built: list[str] = []
    ctx = MagicMock()
    ctx.workspace_scope.root = Path("/workspace")
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target", lambda *_a: "main"
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.observe_conflict_identity",
        lambda _root, target, **_kwargs: ConflictIdentity(
            feature_sha="feature-2", target_sha="target-2", scope="feature"
        ),
    )

    def _build(**_kwargs: Any) -> Any:
        built.append("built")
        return lambda _root, _target: True

    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _build)
    monkeypatch.setattr(run_loop, "_complete_in_progress_merge", lambda *_a: True)

    spent_on_another_conflict = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
        last_conflict_feature_sha="feature-1",
        last_conflict_target_sha="target-1",
        last_conflict_scope="feature",
    )
    assert (
        run_loop._run_integration_conflict_resolution(ctx, spent_on_another_conflict) is True
    )
    assert len(built) == 1

    same_conflict = spent_on_another_conflict.model_copy(
        update={"last_conflict_feature_sha": "feature-2", "last_conflict_target_sha": "target-2"}
    )
    assert run_loop._run_integration_conflict_resolution(ctx, same_conflict) is False
    assert len(built) == 1


def test_resolver_runs_when_no_rebase_state_is_threaded(monkeypatch: Any) -> None:
    """Omitting the state keeps the resolver reachable (no accidental gate)."""
    ctx = MagicMock()
    ctx.workspace_scope.root = Path("/workspace")
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target", lambda *_a: "main"
    )
    monkeypatch.setattr(
        run_loop, "build_agent_conflict_resolver", lambda **_k: (lambda _r, _t: True)
    )
    monkeypatch.setattr(run_loop, "_complete_in_progress_merge", lambda *_a: True)

    assert run_loop._run_integration_conflict_resolution(ctx) is True


def test_completing_a_merge_stages_and_commits_not_just_resolves(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """The resolver alone never clears MERGE_HEAD; the merge must be committed.

    Regression guard: invoking the bare conflict resolver repaired the
    working tree but left the merge uncommitted, so the re-inspection still
    reported "merge is in progress" and the run exited having burned a full
    agent session for nothing.
    """
    from ralph.git.merge import MERGE_STATE_IN_PROGRESS

    calls: list[str] = []
    monkeypatch.setattr(
        "ralph.git.merge.merge_state", lambda _root: MERGE_STATE_IN_PROGRESS
    )

    def _resolve_and_commit(_root: Path, _target: str, _resolver: Any) -> bool:
        calls.append("resolve_and_commit")
        return True

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_resolve._resolve_and_commit", _resolve_and_commit
    )

    assert run_loop._complete_in_progress_merge(tmp_path, "main", lambda _r, _t: True) is True
    assert calls == ["resolve_and_commit"]


def test_completing_a_merge_declines_when_no_merge_is_in_progress(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """With no MERGE_HEAD there is nothing for the merge path to finish."""
    from ralph.git.merge import MERGE_STATE_NONE

    def _must_not_run(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("_resolve_and_commit must not run without a merge")

    monkeypatch.setattr("ralph.git.merge.merge_state", lambda _root: MERGE_STATE_NONE)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_resolve._resolve_and_commit", _must_not_run
    )

    assert run_loop._complete_in_progress_merge(tmp_path, "main", lambda _r, _t: True) is False
