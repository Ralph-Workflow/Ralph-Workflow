"""Regression coverage for integration recovery that outlives a spent ladder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from ralph.config.models import UnifiedConfig
from ralph.git import merge, merge_obstructions
from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline import run_loop, runner
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import AgentsPolicy, PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict


def _noop_warn(_display: object, _msg: str) -> None:
    pass


def _uninstalled_agent(_agent: str) -> None:
    """Registry ``.get`` stub: no resolver agent is installed."""


def _noop_save(_state: PipelineState, _ctx: run_loop._LoopContext) -> None:
    pass


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    result: PipelineState
    exit_code: int | None
    dispatched: tuple[PipelineState, ...]
    saved: tuple[PipelineState, ...]
    warnings: tuple[str, ...]
    waits: tuple[float, ...]


def run_recovery_scenario(
    monkeypatch: MonkeyPatch, state: PipelineState, *, startup: bool = False
) -> RecoveryObservation:
    """Drive the real loop against Git evidence cleared by an injected clock."""
    dispatched: list[PipelineState] = []
    saved: list[PipelineState] = []
    warnings: list[str] = []
    waits: list[float] = []
    recovered = False
    workspace = MemoryWorkspace("/memory/recovery")
    workspace.write("main", "before")
    workspace.write("HEAD", "feature")

    def sleep(delay: float) -> None:
        nonlocal recovered
        assert delay > 0
        assert dispatched == [], "ordinary dispatch must wait for integration recovery"
        assert saved[-1].is_waiting_state is True
        waits.append(delay)
        assert len(waits) <= 2, "recovered repository must allow progress"
        recovered = len(waits) == 2
        if recovered:
            workspace.write("main", workspace.read("HEAD"))

    def save(current: PipelineState, _ctx: run_loop._LoopContext) -> None:
        workspace.write("checkpoint.json", current.model_dump_json())
        saved.append(PipelineState.model_validate_json(workspace.read("checkpoint.json")))

    def inspect(root: Path, rebase: RebaseState) -> IntegrationResolutionVerdict:
        return inspect_integration_resolution(
            root,
            rebase,
            porcelain=lambda _root: (True, "" if recovered else "UU shared.txt\n"),
            rebase_active=lambda _root: False,
            merge_status=lambda _root: MERGE_STATE_NONE,
        )

    def dispatch(*, state: PipelineState, **_kwargs: object) -> int:
        assert recovered, "unresolved integration must never reach the phase executor"
        assert state.is_waiting_state is False
        # The exhaustion latch is released by the containment verdict once
        # the tree is clean; the typed ladder-escalation outcome itself
        # persists on the checkpoint until a successful land replaces it.
        assert saved[-1].rebase.resolution_exhausted is False
        assert workspace.read("main") == workspace.read("HEAD")
        dispatched.append(state)
        return 0

    def warn(_scope: str, _channel: str, message: str) -> None:
        warnings.append(message)

    def exhausted(*_args: object) -> bool:
        return not recovered

    def recover(*_args: object, **_kwargs: object) -> None:
        return None

    def sync_target(_ctx: run_loop._LoopContext) -> tuple[Path, str]:
        return workspace.root, "main"

    def observe_sha(_root: Path, ref: str) -> tuple[str, bool]:
        return workspace.read(ref), True

    def ancestry(_root: Path, ancestor: str, descendant: str) -> bool:
        return workspace.read(ancestor) == workspace.read(descendant)

    def legacy(_root: Path) -> RebaseState:
        return state.rebase

    def startup_outcome(
        current: PipelineState, _ctx: run_loop._LoopContext, **_kwargs: object
    ) -> PipelineState:
        return current

    display = SimpleNamespace(emit_warn_line=warn)
    latest_state: list[PipelineState] = []
    ctx = cast(
        "run_loop._LoopContext",
        SimpleNamespace(
            policy_bundle=SimpleNamespace(
                pipeline=PipelinePolicy(
                    entry_phase=str(state.phase),
                    phases={
                        str(state.phase): PhaseDefinition(
                            drain="development", transitions=PhaseTransition(on_success="complete")
                        )
                    },
                ),
                agents=AgentsPolicy(),
            ),
            active_display=display,
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(workspace.root),
            latest_state=latest_state,
            sleep=sleep,
            pipeline_deps=None,
            connectivity_monitor=SimpleNamespace(current_state=None),
            display_context=None,
            effective_verbosity=None,
            registry=None,
            effective_pipeline_subscriber=None,
            controller=None,
            config_path=None,
            cli_overrides=None,
            monitor_stop=None,
        ),
    )
    monkeypatch.setattr(run_loop, "inspect_integration_resolution", inspect)
    monkeypatch.setattr(run_loop, "_exhaustion_still_binds", exhausted)
    monkeypatch.setattr(run_loop, "_run_integration_conflict_resolution", recover)
    monkeypatch.setattr(run_loop, "_run_startup_integration", recover)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", recover)
    monkeypatch.setattr(run_loop, "_configured_sync_target", sync_target)
    monkeypatch.setattr(merge, "observe_branch_sha", observe_sha)
    monkeypatch.setattr(
        merge_obstructions,
        "ancestry_state",
        ancestry,
    )
    monkeypatch.setattr("ralph.git.rebase.rebase.rebase_in_progress", lambda _root=None: False)
    monkeypatch.setattr(merge, "merge_state", lambda _root: merge.MERGE_STATE_NONE)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", save)
    monkeypatch.setattr(runner, "run_pipeline_step", dispatch)
    monkeypatch.setattr(run_loop, "legacy_rebase_startup_block", legacy)
    monkeypatch.setattr(run_loop, "_apply_startup_rebase_outcomes", startup_outcome)
    loop = run_loop._run_inner_loop if startup else run_loop._run_inner_loop_after_startup
    result, _previous, exit_code = loop(state, ctx, str(state.phase))
    return RecoveryObservation(
        result, exit_code, tuple(dispatched), tuple(saved), tuple(warnings), tuple(waits)
    )


def test_exhausted_conflict_ladder_waits_until_recovery_before_ordinary_dispatch(
    monkeypatch: MonkeyPatch,
) -> None:
    """When the conflict ladder is exhausted, the run loop must
    persist the typed :class:`RebaseOutcomeKind.LADDER_ESCALATION`
    transition through the :meth:`RebaseState.set_ladder_escalation`
    single-writer setter and dispatch ONE session-scoped automated
    resolver handoff (O1 / O3 cold durable exhausted contract).
    The old behavior -- sleep + recovery preamble + strategy
    rotation -- silently replenished the attempt budget and is
    forbidden by the bounded-progress invariant. The replacement
    contract keeps that invariant: no sleep on the first escalation,
    no strategy rotation, no silent cap replenishment; it dispatches
    a single automated resolver handoff ONCE per session so a cold
    durable ``resolution_exhausted=True`` state is not a permanent
    operator-only dead end.

    The test exercises the same setup as before, but instead of
    asserting that the loop ends the run on a person, it asserts
    that the exhausted state escalates through the autonomous
    fallback ladder, one handoff was dispatched, no sleep was
    performed on the first escalation, and a repeated call within
    the same session does NOT re-dispatch (it paces the loop with
    the bounded repeat-escalation retry instead).
    """
    # The run-loop call would loop forever (the integration is still
    # unresolved and the exhausted state escalates without sleeping).
    # Exercise the helper directly instead: the behavior is identical
    # to the loop's first iteration, and proves the typed-setter
    # contract that the run loop relies on.
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="unresolved shared.txt",
            resolution_exhausted=True,
            conflict_strategies_tried=(
                "rebase_resolver: unresolved shared.txt",
                "merge_instead: unresolved shared.txt",
            ),
        ),
    )

    handoff_calls: list[str] = []
    sleeps: list[float] = []

    def _record_handoff(_ctx: object, failure: str, *, backend: object = None) -> None:
        # Capture the dispatch so the test asserts the run loop
        # dispatches exactly one session-scoped handoff on the cold
        # exhausted path (O1 / O3 contract).
        handoff_calls.append(failure)

    workspace = MemoryWorkspace("/memory/recovery")
    workspace.write("main", "before")
    workspace.write("HEAD", "feature")

    def _record_sleep(delay: float) -> None:
        sleeps.append(delay)

    latest_state: list[PipelineState] = []
    ctx = cast(
        "run_loop._LoopContext",
        SimpleNamespace(
            policy_bundle=SimpleNamespace(
                pipeline=PipelinePolicy(
                    entry_phase="development",
                    phases={
                        "development": PhaseDefinition(
                            drain="development", transitions=PhaseTransition(on_success="complete")
                        )
                    },
                ),
                agents=AgentsPolicy(),
            ),
            active_display=SimpleNamespace(emit_warn_line=_noop_warn),
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(workspace.root),
            latest_state=latest_state,
            sleep=_record_sleep,
            connectivity_monitor=SimpleNamespace(current_state=None),
            _session_handoff_dispatched=False,
        ),
    )

    # Mock the automated-resolver handoff so the test does not pull
    # in the full recovery / diagnostic chain; we only need to
    # observe whether the run loop dispatched it.
    monkeypatch.setattr(run_loop, "_dispatch_retained_handoff", _record_handoff)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)

    updated = run_loop._wait_for_integration_retry(state, ctx)

    # The exhausted state is escalated through the typed LADDER_ESCALATION
    # outcome -- never the retired INTERVENTION_REQUIRED operator hand-off.
    # The typed single-writer setter persists the transition. The carried
    # counters and the resolution_exhausted flag survive, so the run-loop
    # consumer that gates dispatch keeps refusing ordinary work.
    assert updated.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert updated.rebase.is_intervention_required is True
    assert updated.rebase.resolution_exhausted is True
    # The conflict-strategy block is preserved: the typed
    # ``LADDER_ESCALATION`` transition does NOT cycle or reset it.
    assert updated.rebase.conflict_strategy_index == state.rebase.conflict_strategy_index
    assert updated.rebase.conflict_strategies_tried == state.rebase.conflict_strategies_tried
    # The cold exhausted state MUST dispatch exactly one session-scoped
    # handoff (O1 / O3 -- not a permanent operator-only dead end).
    assert len(handoff_calls) == 1, (
        "Cold exhausted state must dispatch one session-scoped automated "
        "resolver handoff (O1/O3 cold durable exhausted contract)."
    )
    assert "unresolved shared.txt" in handoff_calls[0]
    # The first escalation does NOT sleep -- a sleep would mask the
    # escalation transition and let the loop continue iterating.
    assert sleeps == [], (
        "First escalation must not sleep; the run loop escalates through "
        "the typed ladder outcome and returns."
    )

    # A repeated call within the SAME session MUST NOT re-dispatch the
    # handoff (the in-session flag prevents the operator-visible rotation
    # that motivated the original guard); it paces the loop with the
    # bounded repeat-escalation retry instead.
    handoff_calls.clear()
    run_loop._wait_for_integration_retry(updated, ctx)
    assert handoff_calls == [], (
        "Same-session calls must not re-dispatch the automated resolver handoff "
        "(O1 cap-bound escalation, no operator-visible rotation)."
    )
    assert sleeps == [run_loop._REPEAT_ESCALATION_RETRY_SECONDS], (
        "Repeat escalation must pace the loop with exactly one bounded retry."
    )


def test_exhausted_conflict_ladder_loop_recovers_without_terminal_code(
    monkeypatch: MonkeyPatch,
) -> None:
    """When the conflict ladder is exhausted, the enclosing run loop must
    NOT make a terminal transition that ends the run on a person. The
    typed LADDER_ESCALATION outcome keeps the loop alive: it blocks for
    recovery, paces repeat escalations with the bounded retry, and
    resumes ordinary dispatch as soon as the autonomous recovery clears
    the Git evidence — no exit code 1, no operator hand-off.
    """
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="unresolved shared.txt",
            resolution_exhausted=True,
            conflict_strategies_tried=(
                "rebase_resolver: unresolved shared.txt",
                "merge_instead: unresolved shared.txt",
            ),
        ),
    )
    observed = run_recovery_scenario(monkeypatch, state)
    # The run never ends on a person: no terminal exit code, ordinary
    # dispatch resumed only after the bounded retries, and the recovery
    # was autonomous (paced waits, no person-dependent outcome).
    assert observed.exit_code != 1
    assert len(observed.dispatched) == 1
    assert observed.waits == (
        run_loop._REPEAT_ESCALATION_RETRY_SECONDS,
        run_loop._REPEAT_ESCALATION_RETRY_SECONDS,
    )


def test_conflict_cap_exhaustion_escalates_through_ladder_never_handoff(
    monkeypatch: MonkeyPatch,
) -> None:
    """Cap-path acceptance test: conflict-cap exhaustion escalates through
    the autonomous fallback ladder and never produces the retired
    operator hand-off.

    Drives the real cap path in :func:`run_loop._wait_for_integration_retry`
    and the real phase-decision authority, asserting:

    (a) a fresh conflict state does NOT carry the intervention latch
        (the person-gate is never the pre-cap state);
    (b) the cap path persists ``last_outcome == LADDER_ESCALATION``
        (never ``INTERVENTION_REQUIRED`` / ``DIAGNOSTIC_INTERVENTION_REQUIRED``);
    (c) the typed authority maps the escalation to
        ``PhaseDecision.BLOCK_FOR_RECOVERY`` (the fallback ladder owns
        the next action);
    (d) the enclosing inner loop never returns exit code 1 from the
        blocked-integration wait — the run stays alive autonomously;
    (e) no display or log text asks for "manual recovery" — the alert
        names the preserved copy's location and the next autonomous
        ladder rung.
    """
    # The state production persists when the conflict budget is spent at
    # the cap: a fresh conflict outcome that carries the durable
    # ``resolution_exhausted`` latch but NOT the intervention gate (the
    # shape written by ``record_resolution_exhausted`` after the resolver
    # chain exhausts the configured cap). Below the cap the retry paces
    # with a bounded sleep and never escalates; only this spent-budget
    # record is the cap path the run loop escalates through the ladder.
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="cap-path unresolved shared.txt",
            consecutive_conflicts=1,
            resolution_exhausted=True,
        ),
    )
    # (a) The pre-cap state never latches the person-gate.
    assert state.rebase.is_intervention_required is False

    handoff_calls: list[str] = []
    sleeps: list[float] = []

    def _record_handoff(_ctx: object, failure: str, *, backend: object = None) -> None:
        handoff_calls.append(failure)

    workspace = MemoryWorkspace("/memory/cap-path")
    workspace.write("main", "before")
    workspace.write("HEAD", "feature")

    ctx = cast(
        "run_loop._LoopContext",
        SimpleNamespace(
            policy_bundle=SimpleNamespace(
                pipeline=PipelinePolicy(
                    entry_phase="development",
                    phases={
                        "development": PhaseDefinition(
                            drain="development", transitions=PhaseTransition(on_success="complete")
                        )
                    },
                ),
                agents=AgentsPolicy(),
            ),
            active_display=SimpleNamespace(emit_warn_line=_noop_warn),
            config=UnifiedConfig.model_validate(
                {"general": {"agent_max_consecutive_unproductive": 1}}
            ),
            workspace_scope=WorkspaceScope(workspace.root),
            latest_state=[],
            sleep=sleeps.append,
            connectivity_monitor=SimpleNamespace(current_state=None),
            _session_handoff_dispatched=False,
            # The below-cap pending-retry path consults
            # ``_resolver_cooldown_wait_ms(ctx)``, which iterates
            # ``resolution_chain_agents(ctx.policy_bundle)`` and reads
            # ``ctx.registry`` / ``ctx.controller`` through
            # ``choose_resolver``. The default ``AgentsPolicy()`` binds no
            # resolution drain, so the chain is empty and neither attribute
            # is dereferenced; provide the stubs anyway so a future
            # parametrization that binds a resolver cannot crash here.
            registry=SimpleNamespace(get=_uninstalled_agent),
            controller=None,
        ),
    )

    monkeypatch.setattr(run_loop, "_dispatch_retained_handoff", _record_handoff)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble_fn)

    updated = run_loop._wait_for_integration_retry(state, ctx)

    # (b) The cap path persists the typed ladder outcome — never the
    # retired operator hand-off outcome.
    assert updated.rebase.is_intervention_required is True
    assert updated.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert updated.rebase.last_outcome not in (
        RebaseOutcomeKind.INTERVENTION_REQUIRED,
        RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
    )
    # The first escalation never sleeps and dispatches exactly one
    # session-scoped automated resolver handoff.
    assert sleeps == []
    assert len(handoff_calls) == 1

    # (c) The typed phase-decision authority routes the escalation to
    # BLOCK_FOR_RECOVERY — the autonomous fallback ladder owns the next
    # action, never a terminal REPORT_INTERVENTION_REQUIRED hand-off.
    decision = decide_phase_transition(
        updated,
        ctx.policy_bundle.pipeline,
        integration_record=None,
        rebase_state=updated.rebase,
    )
    assert decision is PhaseDecision.BLOCK_FOR_RECOVERY
    assert decision is not PhaseDecision.REPORT_INTERVENTION_REQUIRED

    # (e) No "manual recovery needed" text anywhere on the operator
    # surfaces: the alert names the preserved copy's location and the
    # next autonomous ladder rung.
    alert = run_loop._integration_alert_for_state(updated)
    assert alert is not None
    assert "manual recovery" not in alert
    assert "backup refs" in alert
    assert "rung" in alert

    # (d) The enclosing inner loop never ends the run on a person at the
    # blocked-integration wait: drive the real loop with the escalated
    # state and assert it keeps blocking (bounded retries, no terminal
    # exit code) until the Git evidence clears, then resumes dispatch.
    observed = run_recovery_scenario(monkeypatch, updated)
    assert observed.exit_code != 1
    assert len(observed.dispatched) == 1
    assert len(observed.waits) >= 1, "Repeat escalations pace the loop with bounded retries"


def _noop_preamble_fn(
    _scope: WorkspaceScope,
    _config: UnifiedConfig,
    *,
    backend: object = None,
) -> RebaseState | None:
    return None
