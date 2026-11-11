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
    sleeps, ctx = _setup_retry_env(tmp_path)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=True,
        diagnostic_evidence=(("reflog", "matching_evidence"),),
    )
    record = record.model_copy(
        update={"diagnostic_ownership": f"{identity_fingerprint(record)}:{time.time()}"}
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


def test_wait_for_retry_expired_lease_uses_bounded_retry_without_handoff(
    tmp_path: Path,
) -> None:
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
    sleeps, ctx = _setup_retry_env(tmp_path)

    target_file = record_path(tmp_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("CORRUPT_JSON_{not valid")

    initial_state = _make_state()

    updated_state = wait_for_integration_retry(initial_state, ctx)

    _assert_bounded_retry_without_handoff(sleeps, updated_state)

    assert record_path(tmp_path).read_text(encoding="utf-8") == "CORRUPT_JSON_{not valid"


def test_wait_for_retry_carried_count_boundary_with_cooling_agents_stays_pending_and_resumes(
    tmp_path: Path,
) -> None:
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

    assert preamble_called == [True], "Recovery preamble must be invoked before the bounded sleep"

    assert len(sleeps) == 1, (
        f"Below-cap retry must perform exactly one bounded sleep, got {sleeps!r}"
    )

    assert updated.rebase.consecutive_conflicts == 1, (
        "Recovery failure must charge the consecutive-conflict counter"
    )


def test_wait_for_retry_ralph_moved_head_uses_bounded_retry_not_tamper(
    tmp_path: Path,
) -> None:
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
    import subprocess

    sleeps, ctx = _setup_retry_env(tmp_path)

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
    lease = f"{identity_fingerprint(record)}:{time.time()}"
    record = record.model_copy(update={"diagnostic_ownership": lease})
    write_record(tmp_path, record)

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

    _assert_bounded_retry_without_handoff(sleeps, updated_state)

    on_disk = read_record(tmp_path)
    if on_disk is not None:
        assert on_disk.diagnostic_evidence == ()
        assert on_disk.diagnostic_ownership is None
        assert on_disk.diagnostic_intervention_required is False


def test_wait_for_retry_loop_unresolved_progresses_attempt_or_escalates(
    tmp_path: Path,
) -> None:
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

    state = wait_for_integration_retry(state, ctx)
    assert state.rebase.consecutive_conflicts == cap
    assert state.rebase.is_intervention_required is False
    assert len(handoffs) == 0
    assert len(saved_checkpoints) >= 1


def test_wait_for_retry_cold_loaded_capped_ladder_escalation_dispatches_handoff(
    tmp_path: Path,
) -> None:
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
    assert len(handoff_calls) == 1
    assert "cold-loaded detail" in handoff_calls[0]
    assert first_result.rebase.is_intervention_required is True
    assert first_result.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert sleeps == []

    second_result = wait_for_integration_retry(first_result, ctx)
    assert len(handoff_calls) == 1, "Same-session calls must not re-dispatch handoff"
    assert second_result.rebase.is_intervention_required is True
    assert second_result.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert len(sleeps) == 1, "Repeat escalation paces the loop with one bounded retry"


def test_wait_for_retry_repeated_identical_blocked_outcomes_progress_attempts(
    tmp_path: Path,
) -> None:
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

    state = wait_for_integration_retry(state, ctx)
    assert sleeps == [], "Identical blocked outcomes must not sleep before the first escalation"
    assert len(handoffs) == 1
    assert state.rebase.is_intervention_required is True
    assert state.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
    assert state.rebase.consecutive_conflicts == 1

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
        assert state.rebase.consecutive_conflicts == attempt
        assert len(sleeps) == attempt
        assert all(delay > 0 for delay in sleeps)

    assert len(handoffs) == 0
    assert state.rebase.is_intervention_required is False
    assert state.rebase.consecutive_conflicts >= cap


def test_wait_for_retry_successful_handoff_not_overwritten_with_intervention(
    tmp_path: Path,
) -> None:
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
    assert len(handoff_calls) == 1, (
        "Cold exhausted state with live conflict must dispatch one session-scoped "
        "automated resolver handoff (O1/O3 -- not a permanent operator-only dead end)."
    )
    assert "cold exhausted detail with live conflict" in handoff_calls[0]
    assert sleeps == [], "Cold exhausted handoff must not sleep; O1 retry-progress rule"
    assert first_result.rebase.consecutive_conflicts <= cap, (
        f"Counter must be clamped to the cap ({cap}), got "
        f"{first_result.rebase.consecutive_conflicts}"
    )
    assert first_result.rebase.is_intervention_required is True
    assert first_result.rebase.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION

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
    sleeps, ctx = _setup_retry_env(tmp_path)

    handoff_calls: list[str] = []

    ctx.hooks.handoff_retained_failure = lambda _c, d: handoff_calls.append(d)

    cold_exhausted_rebase = RebaseState(
        last_action="conflict",
        last_reason="cold exhausted without live counter cap",
        consecutive_conflicts=1,
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

    for _ in range(cap + 2):
        state = wait_for_integration_retry(state, ctx)
        assert state.rebase.is_intervention_required is False
        assert state.rebase.consecutive_conflicts == 0
        assert state.rebase.last_publication_decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE

    assert len(sleeps) == cap + 2
    assert sleeps == [5.0] * (cap + 2)

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
    sleeps, ctx = _setup_retry_env(tmp_path)

    handoff_calls: list[str] = []

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

    assert sleeps == [], "Successful cold-exhaustion handoff must not sleep; O1 retry-progress rule"
    assert len(handoff_calls) == 1, (
        "Successful cold-exhaustion handoff must be dispatched exactly once "
        "(session-scoped guard prevents repeat handoffs)."
    )
    assert result.rebase.resolution_exhausted is False, (
        "resolution_exhausted was not cleared by the successful handoff; "
        "the next run would re-dispatch and the durable dead-end persists"
    )
    assert result.rebase.consecutive_conflicts < cap, (
        f"consecutive_conflicts must drop below cap after a successful handoff; "
        f"got {result.rebase.consecutive_conflicts} (cap={cap})"
    )
    assert result.rebase.is_intervention_required is False
    assert result.rebase.last_outcome == RebaseOutcomeKind.COMPLETED
    assert "clearing" not in result.rebase.last_reason


def _make_cold_capped_ctx(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    cap: int = 5,
    target: str = "release",
) -> tuple[list[float], LoopContext, dict[str, int]]:
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target",
        lambda _cfg, _root: target,
    )
    ctx.cold_capped_escalation_attempted = False
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
        consecutive_conflicts=5,
    ).model_copy(update={"resolution_exhausted": True})

    result = run_owned_integration_conflict_resolution(ctx, cold_capped)

    assert counters["built"] == 1, (
        f"Cold-capped state must invoke the resolver; "
        f"built={counters['built']}, OLD probe printed "
        "'resolver_calls=0'. The escalation never ran."
    )
    assert counters["rederive"] == 1, (
        "Cold-capped escalation must re-derive from live Git once before running the resolver"
    )
    assert counters["merge"] == 1
    assert result is True
    assert ctx.cold_capped_escalation_attempted is True


def test_cold_capped_escalation_rederive_clears_stale_state_without_invoking_resolver(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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

    assert counters["rederive"] == 1
    assert counters["built"] == 0, (
        "Resolver must not be built when live Git state has no conflict -- "
        "the cached record is stale and the live re-derivation clears it"
    )
    assert result is True
    assert ctx.cold_capped_escalation_attempted is True
    cleared = ctx.latest_state[0]
    assert cleared.rebase.is_intervention_required is False
    assert cleared.rebase.resolution_exhausted is False
    assert cleared.rebase.consecutive_conflicts == 0
    assert cleared.rebase.last_outcome == RebaseOutcomeKind.COMPLETED


def test_cold_capped_escalation_unsuccessful_returns_false_without_spinning(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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

    assert counters["built"] == 1
    assert counters["merge"] == 1
    assert result is False
    assert ctx.cold_capped_escalation_attempted is True
    assert cold_capped.consecutive_conflicts == initial_counter, (
        "Cold-capped escalation must NEVER reset or raise the counter; "
        "the cap is owned by the budget gate, not the escalation"
    )
    assert sleeps == [], (
        "Unsuccessful cold-capped escalation must not sleep; the O1 "
        "retry-progress rule forbids a silent fixed-interval wait"
    )


def test_cold_capped_escalation_is_one_shot_per_session(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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

    first = run_owned_integration_conflict_resolution(ctx, cold_capped)
    assert first is False
    assert counters["built"] == 1
    second = run_owned_integration_conflict_resolution(ctx, cold_capped)
    assert second is False
    assert counters["built"] == 1, (
        "Same-session re-entry must not re-dispatch the cold-capped "
        "escalation; the O1 retry-progress rule forbids unbounded spin"
    )
    assert sleeps == []


def test_cold_capped_escalation_does_not_fire_for_different_conflict_identity(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity

    _sleeps, ctx, counters = _make_cold_capped_ctx(tmp_path, monkeypatch, cap=5)
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

    assert counters["built"] == 1, (
        f"Ordinary path must run for a different-conflict identity; built={counters['built']}"
    )
    assert result is True
    assert ctx.cold_capped_escalation_attempted is False, (
        "Cold-capped escalation must not fire for a different-conflict "
        "identity; the budget is per-identity and the new conflict "
        "inherits a fresh budget via resolver_allowed"
    )
