"""Tests for _wait_for_integration_retry's handling of durable diagnostic records.

The run loop no longer special-cases the durable intervention flag:
``diagnostic_intervention_required`` is the owner seam's typed record
fact (:class:`RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT`, routed to the
autonomous cleanup ladder) and the record loader's load-and-clear case —
never a run-loop hand-off. The retry path performs the ORDINARY bounded
retry for every durable record shape: the recovery preamble re-derives
state from Git, one bounded sleep below the cap pairs the next
iteration with an in-process satisfier, the consecutive-conflict
counter is charged so the cap-bound escalation remains reachable, and
the run is never blocked on a person. Record bytes are never discarded.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    record_path,
    write_record,
)
from ralph.pipeline.operation_evidence import identity_fingerprint
from ralph.pipeline.rebase_state import PublicationDecision, RebaseOutcomeKind, RebaseState
from ralph.pipeline.retry_diagnostic import (
    EVIDENCE_LEASE_SECONDS,
    RetryDiagnosticHooks,
    run_owned_integration_conflict_resolution,
    wait_for_integration_retry,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.recovery.controller import RecoveryController
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.pipeline.run_loop import LoopContext


#: Ceiling for the bounded repeat-escalation pacing sleep asserted below.
#: The production pace is a small tuning constant; this ceiling only
#: rejects a busy spin (0s) or a stall of a minute or more. It never
#: pins the exact pace -- the contract under test is "exactly one
#: positive, bounded pacing sleep per repeat escalation".
_MAX_PACING_SLEEP_SECONDS = 60.0


def _noop_warn(_display: object, _msg: str) -> None:
    pass


def _noop_save(_state: PipelineState, _ctx: LoopContext) -> None:
    pass


def _noop_preamble(
    _scope: WorkspaceScope,
    _config: UnifiedConfig | None,
    *,
    backend: object = None,
) -> RebaseState | None:
    return None


def _create_retry_ctx(
    root: Path,
    sleep_sink: list[float],
) -> LoopContext:
    """Build a minimal typed _LoopContext for _wait_for_integration_retry."""
    latest_state: list[PipelineState] = []
    return cast(
        "LoopContext",
        SimpleNamespace(
            hooks=RetryDiagnosticHooks(),
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


def _setup_retry_env(
    tmp_path: Path,
) -> tuple[list[float], LoopContext]:
    """Build a retry context whose checkpoint writes are observed, not persisted."""
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    ctx.hooks.save_checkpoint = _noop_save
    return sleeps, ctx


def _mock_successful_handoff(loop_ctx: LoopContext, _detail: str) -> None:
    resolved = RebaseState(
        last_outcome=RebaseOutcomeKind.COMPLETED,
        last_reason="conflict resolved by automated resolver",
        consecutive_conflicts=0,
    )
    loop_ctx.latest_state[:] = [
        (
            loop_ctx.latest_state[0]
            if loop_ctx.latest_state
            else PipelineState(phase="development")
        ).copy_with(rebase=resolved)
    ]


def _make_state(conflicts: int = 0, **kw: object) -> PipelineState:
    rebase_kwargs: dict[str, object] = {
        "last_action": "conflict",
        "last_reason": "synchronization unproven",
        "consecutive_conflicts": conflicts,
    }
    rebase_kwargs.update(kw)
    return PipelineState(phase="development", rebase=RebaseState(**rebase_kwargs))


def _assert_bounded_retry_without_handoff(
    sleeps: list[float],
    updated_state: PipelineState,
) -> None:
    """The ordinary bounded retry: one bounded sleep, never a hand-off.

    ``is_intervention_required`` names the ESCALATION, not a person
    (a LADDER_ESCALATION outcome owns the state autonomously); the HARD
    RULE invariant is that the retired person-blocked outcomes are never
    the run's end state.
    """
    assert len(sleeps) == 1, (
        f"Below-cap retry must perform exactly one bounded sleep, got {sleeps!r}"
    )
    assert updated_state.rebase.last_outcome not in (
        RebaseOutcomeKind.INTERVENTION_REQUIRED,
        RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
    )


def test_wait_for_retry_unchanged_durable_record_uses_bounded_retry_without_handoff(
    tmp_path: Path,
) -> None:
    """Matching / UNCHANGED durable record: ordinary bounded retry, no hand-off.

    The durable intervention flag is no longer a run-loop special case:
    it is the owner seam's typed record fact (routed to the autonomous
    cleanup ladder) and the loader's load-and-clear case. The retry
    path performs one bounded sleep below the cap and never blocks on
    a person; the record bytes are never discarded.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=True,
        diagnostic_evidence=(("reflog", "matching_evidence"),),
    )
    # Valid unexpired lease matching record fingerprint
    record = record.model_copy(
        update={"diagnostic_ownership": f"{identity_fingerprint(record)}:{time.time()}"}
    )
    write_record(tmp_path, record)

    initial_state = _make_state()

    updated_state = wait_for_integration_retry(initial_state, ctx)

    _assert_bounded_retry_without_handoff(sleeps, updated_state)
    assert updated_state.is_waiting_state is False

    # The flagged record is load-and-cleared into the autonomous ladder
    # (the record loader's migration): the flag and the held evidence are
    # released, the record itself preserved — never a hand-off.
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False


def test_wait_for_retry_expired_lease_uses_bounded_retry_without_handoff(
    tmp_path: Path,
) -> None:
    """Expired lease (UNKNOWN verdict): ordinary bounded retry, no hand-off."""
    sleeps, ctx = _setup_retry_env(tmp_path)

    expired_held_at = time.time() - (EVIDENCE_LEASE_SECONDS + 120.0)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=True,
        diagnostic_evidence=(("reflog", "stale_evidence"),),
    )
    record = record.model_copy(
        update={"diagnostic_ownership": f"{identity_fingerprint(record)}:{expired_held_at}"}
    )
    write_record(tmp_path, record)

    initial_state = _make_state()

    updated_state = wait_for_integration_retry(initial_state, ctx)

    _assert_bounded_retry_without_handoff(sleeps, updated_state)
    assert updated_state.is_waiting_state is False

    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False


def test_wait_for_retry_changed_record_uses_bounded_retry_without_handoff(
    tmp_path: Path,
) -> None:
    """CHANGED durable record: ordinary bounded retry, no hand-off."""
    sleeps, ctx = _setup_retry_env(tmp_path)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=True,
        diagnostic_evidence=(("reflog", "rewritten_evidence"),),
        diagnostic_ownership=None,
    )
    write_record(tmp_path, record)

    initial_state = _make_state()

    updated_state = wait_for_integration_retry(initial_state, ctx)

    _assert_bounded_retry_without_handoff(sleeps, updated_state)
    assert updated_state.is_waiting_state is False

    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False


def test_wait_for_retry_unparseable_lease_uses_bounded_retry_without_handoff(
    tmp_path: Path,
) -> None:
    """Unparseable lease: ordinary bounded retry, no hand-off."""
    sleeps, ctx = _setup_retry_env(tmp_path)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=True,
        diagnostic_evidence=(("reflog", "some_evidence"),),
        diagnostic_ownership="unparseable_no_colon",
    )
    write_record(tmp_path, record)

    initial_state = _make_state()

    updated_state = wait_for_integration_retry(initial_state, ctx)

    _assert_bounded_retry_without_handoff(sleeps, updated_state)
    assert updated_state.is_waiting_state is False

    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False


def test_wait_for_retry_unreadable_record_uses_bounded_retry_without_handoff(
    tmp_path: Path,
) -> None:
    """Unreadable durable record: ordinary bounded retry, no hand-off.

    The corrupt record is the unreadable-record seam's case (the bytes
    stay untouched on disk as evidence; the owner seam fails closed on
    the unreadable durable fact and routes it to the autonomous cleanup
    ladder). The retry path never ends blocked on a person and never
    overwrites the corrupt bytes.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)

    # Write corrupt payload to the record path
    target_file = record_path(tmp_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("CORRUPT_JSON_{not valid")

    initial_state = _make_state()

    updated_state = wait_for_integration_retry(initial_state, ctx)

    _assert_bounded_retry_without_handoff(sleeps, updated_state)

    # No-discard: the corrupt bytes stay byte-for-byte on disk.
    assert record_path(tmp_path).read_text(encoding="utf-8") == "CORRUPT_JSON_{not valid"


def test_wait_for_retry_carried_count_boundary_with_cooling_agents_stays_pending_and_resumes(
    tmp_path: Path,
) -> None:
    """WU-1: at the carried-count boundary with cooling agents, state remains pending without loss and resumes."""
    from ralph.pipeline.conflict_resolution.resolver_health import bench_resolver
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.controller import RecoveryControllerOptions
    from tests.pipeline._auto_integrate_agent_failure_fixtures import (
        FrozenClock,
        resolution_policy_bundle,
    )

    clock = FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock), ledger=ledger)

    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    ctx.controller = controller
    ctx.registry = {"resolver-a": object(), "resolver-b": object()}
    ctx.policy_bundle = resolution_policy_bundle(("resolver-a", "resolver-b"))

    handoff_calls: list[str] = []
    ctx.hooks.handoff_retained_failure = lambda _c, d: handoff_calls.append(d)

    bench_resolver(controller, "resolver-a", "unproductive attempt")
    bench_resolver(controller, "resolver-b", "unproductive attempt")

    cap = ctx.config.general.agent_max_consecutive_unproductive
    initial_rebase = RebaseState(
        last_action="conflict",
        last_reason="conflict detail for resolver",
        consecutive_conflicts=cap,
        integration_unresolved=True,
    )
    initial_state = PipelineState(
        phase="development",
        rebase=initial_rebase,
    )
    ctx.latest_state[:] = [initial_state]

    result1 = wait_for_integration_retry(initial_state, ctx)
    assert len(handoff_calls) == 0, "Cooling agents must not trigger handoff"
    assert result1.rebase.is_intervention_required is False, (
        "Cooling agents must not set intervention"
    )
    assert result1.rebase.consecutive_conflicts == cap, "No state loss on carried count"
    assert result1.rebase.integration_unresolved is True
    assert len(sleeps) == 1
    assert sleeps[0] > 0

    clock._now += 120.0

    resolved_rebase = initial_rebase.model_copy(
        update={
            "last_outcome": RebaseOutcomeKind.COMPLETED,
            "consecutive_conflicts": 0,
            "integration_unresolved": False,
        }
    )
    ctx.hooks.recovery_preamble = lambda _s, _c, backend=None: resolved_rebase

    result2 = wait_for_integration_retry(result1, ctx)
    assert result2.rebase.is_intervention_required is False
    assert result2.rebase.last_outcome == RebaseOutcomeKind.COMPLETED


def test_wait_for_retry_empty_evidence_durable_record_uses_bounded_retry_without_handoff(
    tmp_path: Path,
) -> None:
    """Empty-evidence flagged record: ordinary bounded retry, no hand-off.

    The durable intervention flag is the owner seam's typed record
    fact, never a run-loop hand-off: the retry path performs the
    bounded retry (recovery preamble, one bounded sleep, charged
    counter) and never blocks the run on a person. The record bytes
    are never discarded.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=True,
        diagnostic_evidence=(),
        diagnostic_ownership=None,
    )
    write_record(tmp_path, record)

    initial_state = _make_state()

    updated_state = wait_for_integration_retry(initial_state, ctx)

    _assert_bounded_retry_without_handoff(sleeps, updated_state)

    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False


def test_wait_for_retry_negative_no_intervention_required_invokes_preamble_then_bounded_sleep(
    tmp_path: Path,
) -> None:
    """Requirement 2(d): Negative test - record with no intervention required.

    Asserts that when ``diagnostic_intervention_required`` is False,
    ``_wait_for_integration_retry`` invokes the recovery preamble
    (live-Git re-derivation, O1 retry-progress rule) and pairs the
    next iteration with a bounded sleep. The sleep is paired with an
    in-process satisfier (the recovery preamble on the next
    iteration), so it is not the old operator-only fixed-interval
    wait that D3 forbade. The conflict counter is charged so the
    cap-bound escalation path remains reachable.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)
    preamble_called: list[bool] = []

    def _recording_preamble(
        _scope: WorkspaceScope,
        _config: UnifiedConfig | None,
        *,
        backend: object = None,
    ) -> RebaseState | None:
        preamble_called.append(True)
        return None

    ctx.hooks.recovery_preamble = _recording_preamble

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=False,
    )
    write_record(tmp_path, record)

    initial_state = _make_state()

    updated = wait_for_integration_retry(initial_state, ctx)

    # 1. Assert recovery preamble WAS invoked (live-Git re-derivation).
    assert preamble_called == [True], "Recovery preamble must be invoked before the bounded sleep"

    # 2. Assert ctx.sleep was called exactly once (bounded retry with
    # in-process satisfier, not the old fixed-interval wait).
    assert len(sleeps) == 1, (
        f"Below-cap retry must perform exactly one bounded sleep, got {sleeps!r}"
    )

    # 3. Assert the consecutive-conflict counter was charged so the
    # cap-bound escalation remains reachable.
    assert updated.rebase.consecutive_conflicts == 1, (
        "Recovery failure must charge the consecutive-conflict counter"
    )


def test_wait_for_retry_ralph_moved_head_uses_bounded_retry_not_tamper(
    tmp_path: Path,
) -> None:
    """AC4: Ralph moving HEAD (snapshot/reset) does not produce a blocked state.

    The diagnostic lease is bound to identity_fingerprint, which isolates
    HEAD/index/worktree movements. The durable intervention flag is never
    a run-loop hand-off: the retry path re-derives state from Git through
    the recovery preamble, performs the bounded retry, and never demands
    manual 'restore original evidence'. The record bytes are not tampered
    with by the retry path.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)

    pre_feature = "a" * 40
    pre_target = "b" * 40
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=pre_feature,
        pre_target_sha=pre_target,
        owning_worktree=str(tmp_path),
        diagnostic_intervention_required=True,
        diagnostic_evidence=(("head", "moved_by_ralph_snapshot"),),
    )
    # Valid unexpired lease with stable identity fingerprint
    lease = f"{identity_fingerprint(record)}:{time.time()}"
    record = record.model_copy(update={"diagnostic_ownership": lease})
    write_record(tmp_path, record)

    initial_state = _make_state()

    updated_state = wait_for_integration_retry(initial_state, ctx)

    _assert_bounded_retry_without_handoff(sleeps, updated_state)
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False


@pytest.mark.subprocess_e2e
def test_wait_for_retry_after_real_git_head_movement_uses_bounded_retry_without_handoff(
    tmp_path: Path,
) -> None:
    """AC4 faithful: HEAD actually moved through a Ralph-like git operation.

    The previous AC4 proof wrote the held record in isolation; this
    test exercises real git commits (the same family of operation Ralph
    uses for snapshot/reset/reintegration) BEFORE the retry call. Even
    though HEAD has physically moved, the durable intervention flag is
    never a run-loop hand-off: the retry path performs the ordinary
    bounded retry, never sleeps on a fixed interval as an end state,
    and never blocks the run on a person. The durable record bytes are
    preserved.
    """
    import subprocess

    sleeps, ctx = _setup_retry_env(tmp_path)

    # Initialise a real git repository so we can move HEAD.
    assert (
        subprocess.run(
            ("git", "init", "-b", "main"),
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ("git", "config", "user.email", "test@example.com"),
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ("git", "config", "user.name", "Test"),
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    assert (
        subprocess.run(
            ("git", "add", "seed.txt"),
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ("git", "commit", "-m", "seed"),
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    head_before = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    pre_feature = head_before
    pre_target = head_before
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=pre_feature,
        pre_target_sha=pre_target,
        owning_worktree=str(tmp_path),
        diagnostic_intervention_required=True,
        diagnostic_evidence=(("head", "will_be_moved_by_ralph_snapshot"),),
    )
    # Valid unexpired lease with stable identity fingerprint
    lease = f"{identity_fingerprint(record)}:{time.time()}"
    record = record.model_copy(update={"diagnostic_ownership": lease})
    write_record(tmp_path, record)

    # Move HEAD through a real git operation (the same family of
    # operation Ralph performs for snapshot/reset/reintegration).
    (tmp_path / "extra.txt").write_text("extra\n", encoding="utf-8")
    assert (
        subprocess.run(
            ("git", "add", "extra.txt"),
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ("git", "commit", "-m", "extra"),
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    head_after = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_after != head_before, "HEAD must have actually moved"

    initial_state = _make_state()

    updated_state = wait_for_integration_retry(initial_state, ctx)

    # The durable intervention flag is never a run-loop hand-off: real
    # HEAD movement or not, the retry path performs the ordinary bounded
    # retry and never blocks the run on a person or a fixed interval.
    _assert_bounded_retry_without_handoff(sleeps, updated_state)

    # The flagged record is load-and-cleared into the autonomous ladder:
    # released entirely or cleared of the flag and held evidence — never
    # a hand-off, never a blocked 'restore original evidence' hold.
    on_disk = read_record(tmp_path)
    if on_disk is not None:
        assert on_disk.diagnostic_evidence == ()
        assert on_disk.diagnostic_ownership is None
        assert on_disk.diagnostic_intervention_required is False


def test_wait_for_retry_loop_unresolved_progresses_attempt_or_escalates(
    tmp_path: Path,
) -> None:
    """O1 retry rule: every retry iteration must either change state or escalate.

    When integration cannot be immediately recovered (preamble returns None),
    iterations must not spin without an in-process satisfier: each iteration
    charges the conflict attempt counter, and reaching the cap escalates to
    automated resolver handoff and intervention_required.
    """
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    saved_checkpoints: list[PipelineState] = []
    ctx.hooks.save_checkpoint = lambda s, _c: saved_checkpoints.append(s)
    ctx.hooks.recovery_preamble = _noop_preamble
    handoffs: list[str] = []
    ctx.hooks.handoff_retained_failure = lambda _c, f: handoffs.append(f)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=False,
    )
    write_record(tmp_path, record)

    cap = ctx.config.general.agent_max_consecutive_unproductive
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="conflict detail",
            consecutive_conflicts=cap - 1,
        ),
    )

    # Iteration 1: charges the final attempt up to cap and remains pending
    state = wait_for_integration_retry(state, ctx)
    assert state.rebase.consecutive_conflicts == cap
    assert state.rebase.is_intervention_required is False
    assert len(handoffs) == 0
    assert len(saved_checkpoints) >= 1


def test_wait_for_retry_cold_loaded_capped_ladder_escalation_dispatches_handoff(
    tmp_path: Path,
) -> None:
    """WU-2 cold-load: capped escalation loaded cold still dispatches handoff.

    A durable record loaded from disk that already carries
    ``is_intervention_required=True`` and is at the consecutive-conflict
    cap must receive a fresh automated-resolution handoff in this
    session, otherwise the previous session's exhausted attempt becomes
    a permanent dead end. A subsequent call within the same session
    must NOT re-attempt the handoff (the in-session flag prevents the
    operator-visible rotation that motivated the original guard); it
    paces the loop with the bounded repeat-escalation retry so the
    ladder seam re-observes without a busy spin.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)

    handoff_calls: list[str] = []

    ctx.hooks.handoff_retained_failure = lambda _c, d: handoff_calls.append(d)

    cap = ctx.config.general.agent_max_consecutive_unproductive
    cold_loaded_rebase = RebaseState(
        last_action="conflict",
        last_reason="cold-loaded detail",
        consecutive_conflicts=cap,
    ).set_ladder_escalation("cold-loaded detail")
    initial_state = PipelineState(phase="development", rebase=cold_loaded_rebase)
    assert initial_state.rebase.is_intervention_required is True

    first_result = wait_for_integration_retry(initial_state, ctx)
    # Cold-loaded capped escalation must dispatch automated resolution
    assert len(handoff_calls) == 1
    assert "cold-loaded detail" in handoff_calls[0]
    assert first_result.rebase.is_intervention_required is True
    assert first_result.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    # The first escalation never sleeps (the handoff was just dispatched).
    assert sleeps == []

    # Subsequent call within the same session must avoid repeat handoffs
    # and pace itself with the bounded repeat-escalation retry.
    second_result = wait_for_integration_retry(first_result, ctx)
    assert len(handoff_calls) == 1, "Same-session calls must not re-dispatch handoff"
    assert second_result.rebase.is_intervention_required is True
    assert second_result.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert len(sleeps) == 1, "Repeat escalation paces the loop with one bounded retry"


def test_wait_for_retry_repeated_identical_blocked_outcomes_progress_attempts(
    tmp_path: Path,
) -> None:
    """WU-2 retry progress: identical blocked outcomes charge attempts and escalate.

    The FIRST escalation never sleeps -- the automated-resolution handoff
    was just dispatched, so a sleep would only mask the escalation
    transition. Every REPEAT escalation in the same session paces the
    loop with exactly ONE bounded retry so the recovery preamble and the
    fallback-ladder seam re-observe on each wake instead of busy
    spinning. The attempt counter advances on EVERY iteration (the
    blocked outcome never replenishes the budget) and the outcome stays
    the typed ladder escalation.

    The pacing is pinned as OBSERVED BEHAVIOUR -- one positive, bounded
    sleep per repeat escalation, the same pace every time -- instead of
    the private module constant that implements it, so the assertion
    survives a retune of the production pace without going white-box.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)
    handoffs: list[str] = []
    ctx.hooks.handoff_retained_failure = lambda _c, d: handoffs.append(d)

    def _fake_preamble(
        _scope: WorkspaceScope,
        _config: UnifiedConfig | None,
        *,
        backend: object = None,
    ) -> RebaseState | None:
        return RebaseState(
            last_reason="blocked state from preamble",
        ).set_ladder_escalation("blocked state from preamble")

    ctx.hooks.recovery_preamble = _fake_preamble

    cap = ctx.config.general.agent_max_consecutive_unproductive
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="identical blocked detail",
            consecutive_conflicts=0,
        ).set_ladder_escalation("identical blocked detail"),
    )

    # Iteration 1: the FIRST escalation charges the attempt and dispatches
    # the single session-scoped handoff WITHOUT sleeping.
    state = wait_for_integration_retry(state, ctx)
    assert sleeps == [], "Identical blocked outcomes must not sleep before the first escalation"
    assert len(handoffs) == 1
    assert state.rebase.is_intervention_required is True
    assert state.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert state.rebase.consecutive_conflicts == 1

    # Iterations 2..cap: every REPEAT escalation charges the attempt and
    # paces the loop with exactly ONE bounded retry -- never a busy spin
    # and never a second handoff in the same session. The contract under
    # test is the OBSERVED pacing (one positive, bounded sleep per
    # repeat escalation, the same pace every time), never the private
    # module constant that happens to implement it.
    for repeat in range(1, cap):
        state = wait_for_integration_retry(state, ctx)
        assert len(handoffs) == 1, "Same-session calls must not re-dispatch the handoff"
        assert state.rebase.consecutive_conflicts == repeat + 1, (
            "Every blocked outcome must charge exactly one attempt"
        )
        assert len(sleeps) == repeat, (
            "Every repeat escalation paces the loop with exactly one bounded retry"
        )
        assert len(set(sleeps)) == 1, (
            "Every repeat escalation must pace with the same bounded retry"
        )
        assert all(0.0 < delay <= _MAX_PACING_SLEEP_SECONDS for delay in sleeps), (
            "Every repeat escalation must pace with one positive, bounded retry"
        )

    assert len(handoffs) == 1
    assert state.rebase.is_intervention_required is True
    assert state.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert state.rebase.consecutive_conflicts >= cap


def test_wait_for_retry_unsuccessful_handoff_records_ladder_escalation_without_sleep(
    tmp_path: Path,
) -> None:
    """WU-2 unsuccessful handoff: handoff that does not progress still escalates."""
    sleeps, ctx = _setup_retry_env(tmp_path)
    ctx.hooks.recovery_preamble = _noop_preamble
    handoff_calls: list[str] = []
    ctx.hooks.handoff_retained_failure = lambda _c, d: handoff_calls.append(d)

    cap = ctx.config.general.agent_max_consecutive_unproductive
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="unsuccessful handoff detail",
            consecutive_conflicts=cap,
        ).set_ladder_escalation("unsuccessful handoff detail"),
    )

    result = wait_for_integration_retry(state, ctx)
    assert sleeps == [], "Unsuccessful handoff must not sleep"
    assert len(handoff_calls) == 1
    assert "unsuccessful handoff detail" in handoff_calls[0]
    assert result.rebase.is_intervention_required is True
    assert result.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION


def test_wait_for_retry_pending_repeated_identical_blocked_outcomes_progress_and_escalate(
    tmp_path: Path,
) -> None:
    """WU-2 pending retry: repeated non-None blocked results charge attempts and back off.

    Below the escalation the retry pairs ONE bounded sleep with the
    recovery preamble on every iteration, so an in-progress recovery
    (e.g. a busy worktree lock) has time to advance state before the
    next observation. A blocked (unresolved) recovery result is charged
    as a failed attempt -- never a silent no-progress loop -- and the
    counter climbs toward the cap that the escalated seam owns.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)
    handoffs: list[str] = []
    ctx.hooks.handoff_retained_failure = lambda _c, d: handoffs.append(d)

    def _fake_preamble(
        _scope: WorkspaceScope,
        _config: UnifiedConfig | None,
        *,
        backend: object = None,
    ) -> RebaseState | None:
        return RebaseState(
            last_action="skipped",
            last_reason="integration worktree busy; recovery will retry",
            recovery_record_retained=True,
        )

    ctx.hooks.recovery_preamble = _fake_preamble

    cap = ctx.config.general.agent_max_consecutive_unproductive
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="pending retry detail",
            consecutive_conflicts=0,
        ),
    )
    assert state.rebase.is_intervention_required is False

    for attempt in range(1, cap + 1):
        state = wait_for_integration_retry(state, ctx)
        # Every blocked recovery result charges exactly one attempt and
        # backs off with exactly one bounded sleep.
        assert state.rebase.consecutive_conflicts == attempt
        assert len(sleeps) == attempt
        assert all(delay > 0 for delay in sleeps)

    assert len(handoffs) == 0
    assert state.rebase.is_intervention_required is False
    assert state.rebase.consecutive_conflicts >= cap


def test_wait_for_retry_successful_handoff_not_overwritten_with_intervention(
    tmp_path: Path,
) -> None:
    """WU-2 successful handoff: does not overwrite a successful handoff with intervention."""
    sleeps, ctx = _setup_retry_env(tmp_path)
    ctx.hooks.handoff_retained_failure = _mock_successful_handoff

    cap = ctx.config.general.agent_max_consecutive_unproductive
    initial_state = _make_state(
        conflicts=cap,
        last_reason="conflict detail for resolver",
        resolution_exhausted=True,
    )
    ctx.latest_state[:] = [initial_state]

    result = wait_for_integration_retry(initial_state, ctx)
    assert sleeps == []
    assert result.rebase.is_intervention_required is False
    assert result.rebase.last_outcome == RebaseOutcomeKind.COMPLETED


def test_wait_for_retry_pending_successful_handoff_not_overwritten_with_intervention(
    tmp_path: Path,
) -> None:
    """WU-2 pending retry: successful handoff at cap does not overwrite with intervention."""
    sleeps, ctx = _setup_retry_env(tmp_path)
    ctx.hooks.handoff_retained_failure = _mock_successful_handoff

    cap = ctx.config.general.agent_max_consecutive_unproductive
    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="pending retry detail",
            consecutive_conflicts=cap - 1,
            resolution_exhausted=True,
        ),
    )
    ctx.latest_state[:] = [initial_state]

    result = wait_for_integration_retry(initial_state, ctx)
    assert sleeps == []
    assert result.rebase.is_intervention_required is False
    assert result.rebase.last_outcome == RebaseOutcomeKind.COMPLETED


def _mock_rederived_preamble(
    _scope: WorkspaceScope,
    _config: UnifiedConfig,
    *,
    backend: object = None,
) -> RebaseState | None:
    return RebaseState(
        last_outcome=RebaseOutcomeKind.COMPLETED,
        last_reason="re-derived from live git",
        consecutive_conflicts=0,
    )


def test_wait_for_retry_absent_record_at_cap_invokes_automated_resolver_or_rederives_state(
    tmp_path: Path,
) -> None:
    """WU-2 absent record: absent record at cap invokes automated resolver or re-derives state."""
    _sleeps, ctx = _setup_retry_env(tmp_path)
    resolver_called: list[bool] = []

    def _mock_resolver(_c: LoopContext, _r: RebaseState | None = None) -> bool:
        resolver_called.append(True)
        return False

    ctx.hooks.conflict_resolution = _mock_resolver
    ctx.hooks.recovery_preamble = _mock_rederived_preamble

    cap = ctx.config.general.agent_max_consecutive_unproductive
    initial_state = _make_state(conflicts=cap, resolution_exhausted=True)
    ctx.latest_state[:] = [initial_state]

    result = wait_for_integration_retry(initial_state, ctx)
    assert resolver_called == [True], "Automated resolver must be invoked for absent record at cap"
    assert result.rebase.is_intervention_required is False
    assert result.rebase.last_outcome == RebaseOutcomeKind.COMPLETED


def test_wait_for_retry_unreadable_record_at_cap_invokes_automated_resolver_or_rederives_state(
    tmp_path: Path,
) -> None:
    """WU-2 unreadable record: unreadable record at cap invokes automated resolver or re-derives state."""
    _sleeps, ctx = _setup_retry_env(tmp_path)
    resolver_called: list[bool] = []

    def _mock_resolver(_c: LoopContext, _r: RebaseState | None = None) -> bool:
        resolver_called.append(True)
        return False

    ctx.hooks.conflict_resolution = _mock_resolver
    ctx.hooks.recovery_preamble = _mock_rederived_preamble

    target_file = record_path(tmp_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("CORRUPT_JSON_{invalid")

    cap = ctx.config.general.agent_max_consecutive_unproductive
    initial_state = _make_state(conflicts=cap, resolution_exhausted=True)
    ctx.latest_state[:] = [initial_state]

    result = wait_for_integration_retry(initial_state, ctx)
    assert resolver_called == [True], (
        "Automated resolver must be invoked for unreadable record at cap"
    )
    assert result.rebase.is_intervention_required is False
    assert result.rebase.last_outcome == RebaseOutcomeKind.COMPLETED


def test_wait_for_retry_unreadable_record_at_cap_unsuccessful_escalates_to_intervention(
    tmp_path: Path,
) -> None:
    """WU-2 unreadable record: unreadable record at cap escalates to intervention if unresolved."""
    sleeps, ctx = _setup_retry_env(tmp_path)
    ctx.hooks.conflict_resolution = lambda _c, _r=None: False

    target_file = record_path(tmp_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("CORRUPT_JSON_{invalid")

    cap = ctx.config.general.agent_max_consecutive_unproductive
    initial_state = _make_state(conflicts=cap, resolution_exhausted=True)
    ctx.latest_state[:] = [initial_state]

    result = wait_for_integration_retry(initial_state, ctx)
    assert sleeps == []
    assert result.rebase.is_intervention_required is True


def test_wait_for_retry_cold_durable_resolution_exhausted_with_live_conflict_dispatches_session_handoff(
    tmp_path: Path,
) -> None:
    """WU-1 O1/O3 cold exhausted-with-live-conflict: dispatch one session handoff.

    A cold-loaded durable ``RebaseState`` that carries
    ``resolution_exhausted=True`` AND a still-binding live conflict
    (e.g. ``consecutive_conflicts >= cap``) must NOT be a permanent
    operator-only dead end. The first call in a fresh session MUST
    dispatch exactly one automated resolver handoff (O1 / O3). A
    subsequent call within the same session MUST NOT re-dispatch
    (the in-session flag prevents the operator-visible rotation
    that motivated the original guard). The consecutive-conflict
    counter is clamped to the effective configured cap, no sleep is
    performed, and intervention is recorded when the handoff does
    not clear the exhausted state.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)
    assert sleeps == []

    handoff_calls: list[str] = []

    def _fake_handoff(loop_ctx: object, detail: str) -> None:
        handoff_calls.append(detail)

    ctx.hooks.handoff_retained_failure = _fake_handoff

    cap = ctx.config.general.agent_max_consecutive_unproductive
    cold_exhausted_rebase = RebaseState(
        last_action="conflict",
        last_reason="cold exhausted detail with live conflict",
        consecutive_conflicts=cap,
    ).model_copy(
        update={
            "resolution_exhausted": True,
            "resolution_exhaustion_reason": "all strategies tried",
        }
    )
    initial_state = PipelineState(phase="development", rebase=cold_exhausted_rebase)
    assert initial_state.rebase.resolution_exhausted is True
    assert initial_state.rebase.consecutive_conflicts >= cap
    assert initial_state.rebase.is_intervention_required is False

    first_result = wait_for_integration_retry(initial_state, ctx)
    # 1. Cold exhausted-with-live-conflict must dispatch exactly one handoff.
    assert len(handoff_calls) == 1, (
        "Cold exhausted state with live conflict must dispatch one session-scoped "
        "automated resolver handoff (O1/O3 -- not a permanent operator-only dead end)."
    )
    assert "cold exhausted detail with live conflict" in handoff_calls[0]
    # 2. No sleep was performed (O1 retry-progress rule).
    assert sleeps == [], "Cold exhausted handoff must not sleep; O1 retry-progress rule"
    # 3. Counter clamped to the effective configured cap (no counter may exceed cap).
    assert first_result.rebase.consecutive_conflicts <= cap, (
        f"Counter must be clamped to the cap ({cap}), got "
        f"{first_result.rebase.consecutive_conflicts}"
    )
    # 4. The typed ladder escalation is recorded (handoff did not clear
    # exhaustion in this mock) — never the retired operator hand-off.
    assert first_result.rebase.is_intervention_required is True
    assert first_result.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION

    # 5. A second call within the SAME session MUST NOT re-dispatch the
    # handoff; it paces the loop with the bounded repeat-escalation retry
    # so the ladder seam re-observes without a busy spin.
    second_result = wait_for_integration_retry(first_result, ctx)
    assert len(handoff_calls) == 1, (
        "Same-session calls must not re-dispatch the automated resolver handoff "
        "(O1 cap-bound escalation, no operator-visible rotation)."
    )
    assert second_result.rebase.is_intervention_required is True
    assert second_result.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert len(sleeps) == 1, "Repeat escalation paces the loop with one bounded retry"


def test_wait_for_retry_cold_exhausted_state_with_no_live_conflict_still_dispatches_session_handoff(
    tmp_path: Path,
) -> None:
    """WU-1 O1 boundary: cold exhausted without live conflict still gets handoff.

    Defensive boundary: even when the consecutive-conflict counter has
    not yet reached the cap, the cold durable ``resolution_exhausted=True``
    flag alone is sufficient to enter the exhausted branch
    (``resolution_exhausted or consecutive_conflicts >= cap``). The first
    session-scoped call MUST still dispatch one handoff -- the O1
    contract forbids using the durable flag as a reason to suppress
    automation forever.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)

    handoff_calls: list[str] = []

    ctx.hooks.handoff_retained_failure = lambda _c, d: handoff_calls.append(d)

    cold_exhausted_rebase = RebaseState(
        last_action="conflict",
        last_reason="cold exhausted without live counter cap",
        consecutive_conflicts=1,  # NOT at cap; only the exhausted flag drives this branch
    ).model_copy(
        update={
            "resolution_exhausted": True,
            "resolution_exhaustion_reason": "all strategies tried (prior session)",
        }
    )
    initial_state = PipelineState(phase="development", rebase=cold_exhausted_rebase)
    assert initial_state.rebase.resolution_exhausted is True
    assert (
        initial_state.rebase.consecutive_conflicts
        < ctx.config.general.agent_max_consecutive_unproductive
    )

    first_result = wait_for_integration_retry(initial_state, ctx)
    assert len(handoff_calls) == 1
    assert sleeps == []
    assert first_result.rebase.is_intervention_required is True

    wait_for_integration_retry(first_result, ctx)
    assert len(handoff_calls) == 1, "Same-session calls must not re-dispatch handoff"


def test_wait_for_retry_prolonged_unreachable_remote_does_not_increment_conflicts_or_set_intervention(
    tmp_path: Path,
) -> None:
    """WU-1 O1/O2/AC8: prolonged unreachable remote does not trip conflict cap or intervention."""
    sleeps, ctx = _setup_retry_env(tmp_path)
    cap = 2

    outage_recovered = RebaseState(
        last_reason="remote tacos unreachable; recovery will retry",
        last_publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        consecutive_conflicts=0,
    )
    ctx.hooks.recovery_preamble = lambda _s, _c, backend=None: outage_recovered

    state = PipelineState(
        phase="development",
        rebase=outage_recovered,
    )
    ctx.latest_state[:] = [state]

    # Retry cap + 2 times (4 times, > cap of 2)
    for _ in range(cap + 2):
        state = wait_for_integration_retry(state, ctx)
        assert state.rebase.is_intervention_required is False
        assert state.rebase.consecutive_conflicts == 0
        assert state.rebase.last_publication_decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE

    assert len(sleeps) == cap + 2
    assert sleeps == [5.0] * (cap + 2)

    # Remote recovers: terminal publication outcome
    restored_recovered = RebaseState(
        last_outcome=RebaseOutcomeKind.REMOTE_PUSHED,
        last_push_status="pushed",
        last_publication_decision=PublicationDecision.PUBLICATION_TERMINAL,
        consecutive_conflicts=0,
    )
    ctx.hooks.recovery_preamble = lambda _s, _c, backend=None: restored_recovered

    final_state = wait_for_integration_retry(state, ctx)
    assert final_state.rebase.is_intervention_required is False
    assert final_state.rebase.consecutive_conflicts == 0
    assert final_state.rebase.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED


def test_wait_for_retry_successful_cold_exhaustion_handoff_clears_state(
    tmp_path: Path,
) -> None:
    """WU-2 successful cold-exhaustion handoff: resolver success clears the state.

    The companion test
    :func:`test_wait_for_retry_cold_durable_resolution_exhausted_with_live_conflict_dispatches_session_handoff`
    proves the cold-exhausted state dispatches one session-scoped
    handoff and (when the handoff is unsuccessful) escalates to
    intervention. PA-001 of the analysis identified a gap: that test
    only proves the handoff was *attempted*, not that a successful
    cold-exhaustion handoff *clears* the state. This test closes
    the gap with a handoff that resolves the durable record on the
    first try.

    O1 retry-progress contract: a successful handoff must return
    immediately without sleeping, the durable record's
    ``resolution_exhausted`` flag must be cleared, the consecutive-
    conflict counter must drop below the cap, intervention must NOT be
    recorded, and the durable record must be persisted via the
    standard recovered-checkpoint path so the next run observes the
    cleared state.
    """
    sleeps, ctx = _setup_retry_env(tmp_path)

    handoff_calls: list[str] = []

    # A mock handoff that successfully resolves the durable record
    # on the first try. Mirrors the contract of an automated
    # resolver that succeeds (e.g. a successful git rebase
    # followed by a clean push).
    def _clearing_handoff(loop_ctx: LoopContext, detail: str) -> None:
        handoff_calls.append(detail)
        cleared = RebaseState(
            last_outcome=RebaseOutcomeKind.COMPLETED,
            last_reason="conflict resolved by automated resolver",
            consecutive_conflicts=0,
        ).clear_resolution_exhausted()
        loop_ctx.latest_state[:] = [
            (
                loop_ctx.latest_state[0]
                if loop_ctx.latest_state
                else PipelineState(phase="development")
            ).copy_with(rebase=cleared)
        ]

    ctx.hooks.handoff_retained_failure = _clearing_handoff

    cap = ctx.config.general.agent_max_consecutive_unproductive
    cold_exhausted_rebase = RebaseState(
        last_action="conflict",
        last_reason="cold exhausted detail cleared by resolver",
        consecutive_conflicts=cap,
    ).model_copy(
        update={
            "resolution_exhausted": True,
            "resolution_exhaustion_reason": "all strategies tried",
        }
    )
    initial_state = PipelineState(phase="development", rebase=cold_exhausted_rebase)
    assert initial_state.rebase.resolution_exhausted is True
    assert initial_state.rebase.consecutive_conflicts >= cap

    result = wait_for_integration_retry(initial_state, ctx)

    # 1. Successful handoff must NOT sleep (O1 retry-progress rule).
    assert sleeps == [], "Successful cold-exhaustion handoff must not sleep; O1 retry-progress rule"
    # 2. The handoff was dispatched exactly once.
    assert len(handoff_calls) == 1, (
        "Successful cold-exhaustion handoff must be dispatched exactly once "
        "(session-scoped guard prevents repeat handoffs)."
    )
    # 3. The durable resolution_exhausted flag was cleared by the
    #    handoff -- the live rebase is no longer in the exhausted
    #    state.
    assert result.rebase.resolution_exhausted is False, (
        "resolution_exhausted was not cleared by the successful handoff; "
        "the next run would re-dispatch and the durable dead-end persists"
    )
    # 4. The consecutive-conflict counter is below the cap.
    assert result.rebase.consecutive_conflicts < cap, (
        f"consecutive_conflicts must drop below cap after a successful handoff; "
        f"got {result.rebase.consecutive_conflicts} (cap={cap})"
    )
    # 5. Intervention must NOT be recorded.
    assert result.rebase.is_intervention_required is False
    # 6. The handoff's resolved outcome is observable on the returned state.
    assert result.rebase.last_outcome == RebaseOutcomeKind.COMPLETED
    assert "clearing" not in result.rebase.last_reason


# ---------------------------------------------------------------------------
# WU-1 cold-capped escalation tests (O1 retry-progress rule)
# ---------------------------------------------------------------------------
#
# The cold-capped escalation runs INSIDE
# ``_run_owned_integration_conflict_resolution`` when the cached
# ``RebaseState`` carries ``resolution_exhausted=True`` OR
# ``consecutive_conflicts >= cap`` AND ``resolver_allowed`` returns
# False (the exhaustion binds the conflict on disk now). The OLD
# code returned False WITHOUT consulting a resolver, so a
# cold-loaded capped state was an unresolvable dead end. The
# escalation re-derives from live Git and runs ONCE per session.
# The probe in PA-001 printed ``result=False,
# live_operation_checked=0, resolver_calls=0``; the new tests
# assert ``resolver_calls > 0`` and the successful/unsuccessful
# branches each behave correctly.


def _make_cold_capped_ctx(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    cap: int = 5,
    target: str = "release",
) -> tuple[list[float], LoopContext, dict[str, int]]:
    """Set up a minimal context with a low conflict cap so cold-cap is reachable."""
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    # The integration target resolution requires a real local branch
    # (it consults ``observe_branch_sha``), which is unavailable in a
    # ``tmp_path`` workspace. Stub it so the seam gets a valid target.
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target",
        lambda _cfg, _root: target,
    )
    ctx.cold_capped_escalation_attempted = False
    # ``build_agent_conflict_resolver`` requires ``ctx.registry``,
    # ``ctx.pipeline_deps`` and ``ctx.display_context`` to thread into
    # the agent resolver builder. The minimal ``_create_retry_ctx``
    # doesn't carry them; tests that exercise the escalation path stub
    # ``build_agent_conflict_resolver`` itself, so these attributes are
    # not consulted -- but the underlying builders tolerate the empty
    # SimpleNamespace (``_build`` is mocked out).
    ctx.registry = SimpleNamespace()
    ctx.pipeline_deps = None
    ctx.display_context = None
    ctx.hooks.save_checkpoint = _noop_save
    counters: dict[str, int] = {"built": 0, "merge": 0, "orphan": 0, "rederive": 0, "paused": 0}
    return sleeps, ctx, counters


def test_cold_capped_conflict_invokes_resolver_when_budget_already_spent(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-1 O1: cold-capped conflict must invoke a resolver, not return False.

    The OLD behaviour: ``_run_owned_integration_conflict_resolution``
    returned False when ``resolver_allowed`` was False, leaving a
    cold-capped ``RebaseState(consecutive_conflicts=cap,
    resolution_exhausted=True)`` unresolvable. The new cold-capped
    escalation re-derives from live Git and builds the resolver once
    per session. With cap=5 and ``consecutive_conflicts=5`` the
    state is cold-capped; the assertion is that the resolver is
    actually built and run, not skipped.
    """
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity

    _sleeps, ctx, counters = _make_cold_capped_ctx(tmp_path, monkeypatch, cap=5)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.observe_conflict_identity",
        lambda _root, _target, **_k: ConflictIdentity(),
        raising=False,
    )

    def _fake_build(**_kwargs: object) -> object:
        counters["built"] += 1
        return lambda _root, _target: True

    def _fake_merge(_root: object, _target: object, _resolver: object) -> bool:
        counters["merge"] += 1
        return True

    ctx.hooks.paused_rebase_at = lambda _r: False
    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _fake_build)
    ctx.hooks.complete_in_progress_merge = _fake_merge
    ctx.hooks.resolve_orphaned_unmerged_index = lambda _c, _t: (
        counters.__setitem__("orphan", counters["orphan"] + 1) or False
    )
    ctx.hooks.rederive_live_conflict_state = lambda _r, _t, _rb: (
        counters.__setitem__("rederive", counters["rederive"] + 1) or None
    )

    cold_capped = RebaseState(
        last_action="conflict",
        last_target="release",
        consecutive_conflicts=5,  # == cap
    ).model_copy(update={"resolution_exhausted": True})

    result = run_owned_integration_conflict_resolution(ctx, cold_capped)

    # 1. The resolver was built (the OLD code never built it).
    assert counters["built"] == 1, (
        f"Cold-capped state must invoke the resolver; "
        f"built={counters['built']}, OLD probe printed "
        "'resolver_calls=0'. The escalation never ran."
    )
    # 2. The live state was re-derived once (Step 1 of the escalation).
    assert counters["rederive"] == 1, (
        "Cold-capped escalation must re-derive from live Git once before running the resolver"
    )
    # 3. The escalation succeeded via _complete_in_progress_merge.
    assert counters["merge"] == 1
    assert result is True
    # 4. The session flag was set so a same-session repeat call does not
    # re-dispatch.
    assert ctx.cold_capped_escalation_attempted is True


def test_cold_capped_escalation_rederive_clears_stale_state_without_invoking_resolver(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-1 O1: when live Git state shows no conflict, cached exhaustion is stale.

    The cold-capped escalation first re-derives from live Git. When the
    live state is clean (the cached record is stale -- a concurrent
    push, a previous worktree's resolution, or a recovery preamble
    succeeded between the cold-load and this call), the escalation
    clears the cached flags and returns True WITHOUT invoking the
    resolver. This is the fast path that prevents the cap from being
    burned on a state that no longer exists.
    """
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity

    _sleeps, ctx, counters = _make_cold_capped_ctx(tmp_path, monkeypatch, cap=5)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.observe_conflict_identity",
        lambda _root, _target, **_k: ConflictIdentity(),
        raising=False,
    )

    ctx.hooks.paused_rebase_at = lambda _r: False

    def _fake_rederive(_root: object, _target: object, rebase: RebaseState) -> RebaseState:
        counters["rederive"] += 1
        return rebase.model_copy(
            update={
                "is_intervention_required": False,
                "resolution_exhausted": False,
                "consecutive_conflicts": 0,
                "integration_unresolved": False,
                "last_outcome": RebaseOutcomeKind.COMPLETED,
                "last_reason": "live re-derivation cleared the cold-capped state",
            }
        )

    ctx.hooks.rederive_live_conflict_state = _fake_rederive

    def _fail_build(**_kwargs: object) -> object:
        counters["built"] += 1
        raise AssertionError("resolver must NOT be built when live re-derivation clears the state")

    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _fail_build)

    cold_capped = RebaseState(
        last_action="conflict",
        last_target="release",
        consecutive_conflicts=5,
    ).model_copy(update={"resolution_exhausted": True})
    initial_state = PipelineState(phase="development", rebase=cold_capped)
    ctx.latest_state[:] = [initial_state]

    result = run_owned_integration_conflict_resolution(ctx, cold_capped)

    # 1. Live re-derivation ran once.
    assert counters["rederive"] == 1
    # 2. The resolver was NOT built (live state was clean).
    assert counters["built"] == 0, (
        "Resolver must not be built when live Git state has no conflict -- "
        "the cached record is stale and the live re-derivation clears it"
    )
    # 3. The function returned True (the caller persists the cleared state).
    assert result is True
    # 4. The session flag was set.
    assert ctx.cold_capped_escalation_attempted is True
    # 5. The cleared state is observable on ctx.latest_state.
    cleared = ctx.latest_state[0]
    assert cleared.rebase.is_intervention_required is False
    assert cleared.rebase.resolution_exhausted is False
    assert cleared.rebase.consecutive_conflicts == 0
    assert cleared.rebase.last_outcome == RebaseOutcomeKind.COMPLETED


def test_cold_capped_escalation_unsuccessful_returns_false_without_spinning(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-1 O1: unsuccessful cold-capped escalation exits without operator-only spin.

    When the resolver FAILS, the cold-capped escalation must return
    False WITHOUT sleeping and WITHOUT setting
    ``is_intervention_required`` on the cached rebase (the caller
    already escalates to ``INTERVENTION_REQUIRED`` on the next cold
    call). The cap is NEVER reset or raised -- the failure is bounded,
    and the next cold-capped call falls through to the budget gate.
    """
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity

    sleeps, ctx, counters = _make_cold_capped_ctx(tmp_path, monkeypatch, cap=5)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.observe_conflict_identity",
        lambda _root, _target, **_k: ConflictIdentity(),
        raising=False,
    )

    ctx.hooks.paused_rebase_at = lambda _r: False

    def _fake_build(**_kwargs: object) -> object:
        counters["built"] += 1
        return lambda _root, _target: False

    def _fake_merge(_root: object, _target: object, _resolver: object) -> bool:
        counters["merge"] += 1
        return False

    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _fake_build)
    ctx.hooks.complete_in_progress_merge = _fake_merge
    ctx.hooks.resolve_orphaned_unmerged_index = lambda _c, _t: False
    ctx.hooks.rederive_live_conflict_state = lambda _r, _t, _rb: None

    cold_capped = RebaseState(
        last_action="conflict",
        last_target="release",
        consecutive_conflicts=5,
    ).model_copy(update={"resolution_exhausted": True})
    initial_counter = cold_capped.consecutive_conflicts

    result = run_owned_integration_conflict_resolution(ctx, cold_capped)

    # 1. The resolver was built and run exactly once.
    assert counters["built"] == 1
    assert counters["merge"] == 1
    # 2. The function returned False (escalation did not clear the state).
    assert result is False
    # 3. The session flag is set so a same-session repeat call does not spin.
    assert ctx.cold_capped_escalation_attempted is True
    # 4. The counter was NOT reset or raised by the escalation -- it is still
    # at the cap. The escalation is purely additive and never weakens the cap.
    assert cold_capped.consecutive_conflicts == initial_counter, (
        "Cold-capped escalation must NEVER reset or raise the counter; "
        "the cap is owned by the budget gate, not the escalation"
    )
    # 5. No sleep was performed -- the O1 retry-progress rule forbids a
    # silent fixed-interval spin on a failed escalation.
    assert sleeps == [], (
        "Unsuccessful cold-capped escalation must not sleep; the O1 "
        "retry-progress rule forbids a silent fixed-interval wait"
    )


def test_cold_capped_escalation_is_one_shot_per_session(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-1 O1: a same-session repeat call does not re-dispatch the escalation.

    The ``_cold_capped_escalation_attempted`` flag guarantees that the
    escalation runs at most once per session. The second call falls
    through to the ordinary budget gate which escalates to
    ``INTERVENTION_REQUIRED``. This prevents unbounded spin on a
    truly unresolvable cold conflict -- a fixed-interval wait that
    nothing satisfies is forbidden by O1.
    """
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity

    sleeps, ctx, counters = _make_cold_capped_ctx(tmp_path, monkeypatch, cap=5)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.observe_conflict_identity",
        lambda _root, _target, **_k: ConflictIdentity(),
        raising=False,
    )

    ctx.hooks.paused_rebase_at = lambda _r: False

    def _fake_build(**_kwargs: object) -> object:
        counters["built"] += 1
        return lambda _root, _target: False

    def _fake_merge(_root: object, _target: object, _resolver: object) -> bool:
        counters["merge"] += 1
        return False

    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _fake_build)
    ctx.hooks.complete_in_progress_merge = _fake_merge
    ctx.hooks.resolve_orphaned_unmerged_index = lambda _c, _t: False
    ctx.hooks.rederive_live_conflict_state = lambda _r, _t, _rb: None

    cold_capped = RebaseState(
        last_action="conflict",
        last_target="release",
        consecutive_conflicts=5,
    ).model_copy(update={"resolution_exhausted": True})

    # First call: escalation runs.
    first = run_owned_integration_conflict_resolution(ctx, cold_capped)
    assert first is False
    assert counters["built"] == 1
    # Second call within the SAME session: escalation skipped, budget gate fires.
    second = run_owned_integration_conflict_resolution(ctx, cold_capped)
    assert second is False
    # The resolver must NOT have been built a second time -- the session
    # flag prevents unbounded spin (O1 forbids fixed-interval waits).
    assert counters["built"] == 1, (
        "Same-session re-entry must not re-dispatch the cold-capped "
        "escalation; the O1 retry-progress rule forbids unbounded spin"
    )
    # No sleep was performed (the function returns False immediately).
    assert sleeps == []


def test_cold_capped_escalation_does_not_fire_for_different_conflict_identity(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-1 O1: a different conflict inherits a fresh budget, not the cold-capped escalation.

    The budget is per-identity (a genuinely new conflict must not
    inherit an older conflict's exhausted count). When the cached
    state carries ``consecutive_conflicts >= cap`` but the live
    conflict identity differs, ``resolver_allowed`` returns True
    and the ordinary resolver path runs -- the cold-capped
    escalation must NOT fire (the cached counter was about a
    DIFFERENT conflict).
    """
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity

    _sleeps, ctx, counters = _make_cold_capped_ctx(tmp_path, monkeypatch, cap=5)
    # Live identity is NEW (feature-2/target-2), the cached exhaustion was
    # about feature-1/target-1. resolver_allowed returns True on this
    # mismatch; the cold-capped escalation must NOT fire.
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.observe_conflict_identity",
        lambda _root, _target, **_k: ConflictIdentity(
            feature_sha="feature-2", target_sha="target-2", scope="feature"
        ),
        raising=False,
    )

    ctx.hooks.paused_rebase_at = lambda _r: False

    def _fake_build(**_kwargs: object) -> object:
        counters["built"] += 1
        return lambda _root, _target: True

    def _fake_merge(_root: object, _target: object, _resolver: object) -> bool:
        counters["merge"] += 1
        return True

    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _fake_build)
    ctx.hooks.complete_in_progress_merge = _fake_merge
    ctx.hooks.resolve_orphaned_unmerged_index = lambda _c, _t: False
    ctx.hooks.rederive_live_conflict_state = lambda _r, _t, _rb: None

    spent_on_another_conflict = RebaseState(
        last_action="conflict",
        last_target="release",
        consecutive_conflicts=5,
        last_conflict_feature_sha="feature-1",
        last_conflict_target_sha="target-1",
        last_conflict_scope="feature",
    ).model_copy(update={"resolution_exhausted": True})

    result = run_owned_integration_conflict_resolution(ctx, spent_on_another_conflict)

    # 1. The resolver ran via the ordinary path (the cold-capped escalation
    # must NOT have fired).
    assert counters["built"] == 1, (
        f"Ordinary path must run for a different-conflict identity; built={counters['built']}"
    )
    assert result is True
    # 2. The cold-capped flag was NOT set (the escalation did not fire).
    assert ctx.cold_capped_escalation_attempted is False, (
        "Cold-capped escalation must not fire for a different-conflict "
        "identity; the budget is per-identity and the new conflict "
        "inherits a fresh budget via resolver_allowed"
    )
