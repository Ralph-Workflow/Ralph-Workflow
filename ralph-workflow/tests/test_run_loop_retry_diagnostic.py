"""Tests for _wait_for_integration_retry durable diagnostic intervention handling.

Verifies that when an on-disk record requires diagnostic intervention,
it evaluates diagnostic evidence and makes immediate typed progress
without calling the fixed-interval retry sleep (5.0s):
- UNCHANGED / UNKNOWN (fail-open): evidence cleared, immediate return.
- CHANGED / unreadable: escalated to INTERVENTION_REQUIRED, immediate return.
- No durable intervention required: falls through to existing 5.0s sleep.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._integration_diagnostic import EVIDENCE_LEASE_SECONDS
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    record_path,
    write_record,
)
from ralph.pipeline.operation_evidence import identity_fingerprint
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.recovery.controller import RecoveryController
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _noop_warn(_display: object, _msg: str) -> None:
    pass


def _noop_save(_state: PipelineState, _ctx: run_loop._LoopContext) -> None:
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


def _setup_retry_env(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> tuple[list[float], run_loop._LoopContext]:
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    return sleeps, ctx


def _mock_successful_handoff(loop_ctx: run_loop._LoopContext, _detail: str) -> None:
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


def test_wait_for_retry_unchanged_durable_record_clears_evidence_without_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Requirement 2(a): Matching / UNCHANGED durable record.

    Asserts that _wait_for_integration_retry does not call ctx.sleep,
    durable evidence is cleared from disk, and it returns immediately.
    """
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

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

    updated_state = run_loop._wait_for_integration_retry(initial_state, ctx)

    # 1. Assert ctx.sleep was NOT called
    assert sleeps == [], "Matching/UNCHANGED record must not sleep; immediate return required"

    # 2. Assert durable evidence is cleared on disk
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False

    # 3. Assert returned state cleared waiting and is not in intervention required
    assert updated_state.is_waiting_state is False
    assert updated_state.rebase.is_intervention_required is False


def test_wait_for_retry_expired_lease_unknown_clears_evidence_without_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Requirement 2(b): Expired lease (UNKNOWN verdict).

    Asserts that _wait_for_integration_retry fails open, clears durable
    evidence without calling ctx.sleep, and returns immediately.
    """
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

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

    updated_state = run_loop._wait_for_integration_retry(initial_state, ctx)

    # 1. Assert ctx.sleep was NOT called
    assert sleeps == [], "Expired lease (UNKNOWN) must not sleep; immediate return required"

    # 2. Assert durable evidence is cleared on disk
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False

    # 3. Assert returned state
    assert updated_state.is_waiting_state is False
    assert updated_state.rebase.is_intervention_required is False


def test_wait_for_retry_changed_record_fails_open_and_does_not_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Requirement 2(c): CHANGED durable record fails open (O1).

    A record whose held evidence has no valid lease (CHANGED verdict)
    must not block the run on a fixed-interval wait. The guard
    clears the held evidence, marks the durable record for
    Git re-derivation on the next seam, and returns immediately.
    """
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

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

    updated_state = run_loop._wait_for_integration_retry(initial_state, ctx)

    # 1. Assert ctx.sleep was NOT called (O1: no fixed-interval wait
    # for an unverifiable durable state).
    assert sleeps == [], "CHANGED record must not sleep; O1 fail-open path"

    # 2. Assert durable evidence is cleared on disk.
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False

    # 3. Assert returned state is not in INTERVENTION_REQUIRED.
    assert updated_state.is_waiting_state is False
    assert updated_state.rebase.is_intervention_required is False


def test_wait_for_retry_unparseable_lease_fails_open_and_does_not_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """An unparseable lease fails open and does not sleep (O1)."""
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

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

    updated_state = run_loop._wait_for_integration_retry(initial_state, ctx)

    # 1. Assert ctx.sleep was NOT called.
    assert sleeps == [], "Unparseable lease record must not sleep; O1 fail-open path"

    # 2. Assert durable evidence is cleared on disk.
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False

    # 3. Assert returned state is not in INTERVENTION_REQUIRED.
    assert updated_state.is_waiting_state is False
    assert updated_state.rebase.is_intervention_required is False


def test_wait_for_retry_unreadable_record_escalates_to_intervention_without_cooldown_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Requirement 2(c): Unreadable record.

    Asserts that _wait_for_integration_retry escalates to INTERVENTION_REQUIRED
    when the durable record file is unreadable, and follows the O1
    retry-progress rule: never sleeps on a fixed interval. The recovery
    preamble is invoked to attempt live-Git re-derivation; when it
    cannot clear the durable intervention flag, the consecutive-conflict
    counter is charged and the typed INTERVENTION_REQUIRED transition
    is persisted (capped escalation, no sleep).
    """
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

    # Write corrupt payload to the record path
    target_file = record_path(tmp_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("CORRUPT_JSON_{not valid")

    initial_state = _make_state()

    updated_state = run_loop._wait_for_integration_retry(initial_state, ctx)

    # 1. Assert ctx.sleep was NOT called (O1: no fixed-interval wait).
    assert sleeps == [], "Unreadable record must not sleep; O1 retry-progress rule"

    # 2. Assert raw corrupt bytes preserved for forensics (WU-1)
    evidence_file = record_path(tmp_path).with_name("corrupt_record_evidence.json")
    assert evidence_file.exists()
    assert evidence_file.read_text(encoding="utf-8") == "CORRUPT_JSON_{not valid"

    # 3. Assert INTERVENTION_REQUIRED is set
    assert updated_state.rebase.is_intervention_required is True
    assert updated_state.rebase.last_outcome in (
        RebaseOutcomeKind.INTERVENTION_REQUIRED,
        RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
    )
    assert "unreadable" in (updated_state.rebase.last_reason or "")


def test_wait_for_retry_consecutive_conflicts_cap_performs_single_automated_resolver_handoff(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-2: consecutive conflicts cap triggers single automated resolver handoff."""
    _sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

    handoff_calls: list[str] = []

    def _fake_handoff(loop_ctx: object, detail: str) -> None:
        handoff_calls.append(detail)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _fake_handoff)

    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="conflict detail for resolver",
            consecutive_conflicts=ctx.config.conflict_resolution.max_consecutive_resolver_attempts,
        ),
    )

    first_result = run_loop._wait_for_integration_retry(initial_state, ctx)
    assert len(handoff_calls) == 1
    assert "conflict detail for resolver" in handoff_calls[0]
    assert first_result.rebase.is_intervention_required is True
    assert (
        first_result.rebase.consecutive_conflicts
        >= ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    )

    # Subsequent call with is_intervention_required=True must avoid repeat handoffs
    second_result = run_loop._wait_for_integration_retry(first_result, ctx)
    assert len(handoff_calls) == 1, "Must avoid repeat automated resolver handoff"
    assert second_result.rebase.is_intervention_required is True


def test_wait_for_retry_empty_evidence_durable_record_escalates_to_intervention_without_cooldown_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Requirement: Empty evidence record escalates via live-Git re-derivation.

    The O1 retry-progress rule forbids a fixed-interval wait when the
    durable record demands intervention. The retry path first asks the
    recovery preamble to re-derive state from Git; if recovery cannot
    clear the intervention flag, the consecutive-conflict counter is
    charged and the typed INTERVENTION_REQUIRED transition is
    persisted (capped escalation, no sleep). The forensic bytes of any
    held evidence are preserved for the operator.
    """
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

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

    updated_state = run_loop._wait_for_integration_retry(initial_state, ctx)

    assert sleeps == [], "Empty evidence record must not sleep; O1 retry-progress rule"
    assert updated_state.rebase.is_intervention_required is True
    assert updated_state.rebase.last_outcome in (
        RebaseOutcomeKind.INTERVENTION_REQUIRED,
        RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
    )


def test_wait_for_retry_negative_no_intervention_required_invokes_preamble_then_bounded_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
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
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    preamble_called: list[bool] = []

    def _recording_preamble(
        _scope: WorkspaceScope,
        _config: UnifiedConfig,
        *,
        backend: object = None,
    ) -> RebaseState | None:
        preamble_called.append(True)
        return None

    monkeypatch.setattr(
        run_loop,
        "_run_auto_integrate_recovery_preamble",
        _recording_preamble,
    )

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=False,
    )
    write_record(tmp_path, record)

    initial_state = _make_state()

    updated = run_loop._wait_for_integration_retry(initial_state, ctx)

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


def test_wait_for_retry_ralph_moved_head_rederives_git_progress_not_tamper(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """AC4: Ralph moving HEAD (snapshot/reset) does not produce a blocked 'evidence changed' state.

    The diagnostic lease is bound to identity_fingerprint, which isolates
    HEAD/index/worktree movements. When Ralph resets or snapshots HEAD,
    the diagnostic path re-derives state from Git, clears durable evidence,
    and makes progress without demanding manual 'restore original evidence'.
    """
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

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

    updated_state = run_loop._wait_for_integration_retry(initial_state, ctx)

    # 1. Must NOT sleep on a fixed interval wait (fail-open / UNCHANGED lease)
    assert sleeps == []

    # 2. Durable evidence must be cleared on disk (no 'restore original evidence' hold)
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False

    # 3. Must not report intervention required
    assert updated_state.rebase.is_intervention_required is False


@pytest.mark.subprocess_e2e
def test_wait_for_retry_after_real_git_head_movement_clears_evidence_without_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """AC4 faithful: HEAD actually moved through a Ralph-like git operation.

    The previous AC4 proof at :470-488 wrote the held record in
    isolation and asserted the lease released. The complementary test
    below exercises a real ``git reset --hard`` (the same family of
    operation Ralph uses for snapshot/reset/reintegration) BEFORE the
    retry call. The held lease is bound to ``identity_fingerprint``,
    which isolates HEAD/index/worktree movements: even though HEAD has
    physically moved, the fingerprint is stable, the durable evidence
    is cleared on disk, and the retry loop never sleeps on a fixed
    interval.
    """
    import subprocess

    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

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

    updated_state = run_loop._wait_for_integration_retry(initial_state, ctx)

    # 1. Must NOT sleep on a fixed interval wait (fail-open / UNCHANGED lease)
    assert sleeps == [], "Real HEAD movement must not sleep; fail-open contract"

    # 2. Durable evidence must be cleared on disk (no 'restore original evidence' hold)
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False

    # 3. Must not report intervention required
    assert updated_state.rebase.is_intervention_required is False


def test_wait_for_retry_loop_unresolved_progresses_attempt_or_escalates(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
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
    monkeypatch.setattr(
        run_loop, "_save_recovered_rebase_checkpoint", lambda s, _c: saved_checkpoints.append(s)
    )
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)
    handoffs: list[str] = []
    monkeypatch.setattr(run_loop, "_handoff_retained_failure", lambda _c, f: handoffs.append(f))

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=False,
    )
    write_record(tmp_path, record)

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="conflict detail",
            consecutive_conflicts=cap - 1,
        ),
    )

    # Iteration 1: charges the final attempt up to cap and immediately escalates
    state = run_loop._wait_for_integration_retry(state, ctx)
    assert state.rebase.consecutive_conflicts == cap
    assert state.rebase.is_intervention_required is True
    assert len(handoffs) == 1
    assert "conflict detail" in handoffs[0]
    assert len(saved_checkpoints) >= 1


def test_wait_for_retry_cold_loaded_capped_intervention_dispatches_handoff(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-2 cold-load: capped intervention loaded cold still dispatches handoff.

    A durable record loaded from disk that already carries
    ``is_intervention_required=True`` and is at the consecutive-conflict
    cap must receive a fresh automated-resolution handoff in this
    session, otherwise the previous session's exhausted attempt becomes
    a permanent dead end. A subsequent call within the same session
    must NOT re-attempt the handoff (the in-session flag prevents the
    operator-visible rotation that motivated the original guard).
    """
    _sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

    handoff_calls: list[str] = []

    monkeypatch.setattr(
        run_loop, "_handoff_retained_failure", lambda _c, d: handoff_calls.append(d)
    )

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    cold_loaded_rebase = RebaseState(
        last_action="conflict",
        last_reason="cold-loaded detail",
        consecutive_conflicts=cap,
    ).set_intervention_required("cold-loaded detail")
    initial_state = PipelineState(phase="development", rebase=cold_loaded_rebase)
    assert initial_state.rebase.is_intervention_required is True

    first_result = run_loop._wait_for_integration_retry(initial_state, ctx)
    # Cold-loaded capped intervention must dispatch automated resolution
    assert len(handoff_calls) == 1
    assert "cold-loaded detail" in handoff_calls[0]
    assert first_result.rebase.is_intervention_required is True

    # Subsequent call within the same session must avoid repeat handoffs.
    second_result = run_loop._wait_for_integration_retry(first_result, ctx)
    assert len(handoff_calls) == 1, "Same-session calls must not re-dispatch handoff"
    assert second_result.rebase.is_intervention_required is True


def test_wait_for_retry_repeated_identical_blocked_outcomes_progress_attempts(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-2 retry progress: identical blocked outcomes charge attempts and escalate."""
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    handoffs: list[str] = []
    monkeypatch.setattr(run_loop, "_handoff_retained_failure", lambda _c, d: handoffs.append(d))

    def _fake_preamble(
        _scope: WorkspaceScope,
        _config: UnifiedConfig,
        *,
        backend: object = None,
    ) -> RebaseState | None:
        return RebaseState(
            last_reason="blocked state from preamble",
        ).set_intervention_required("blocked state from preamble")

    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _fake_preamble)

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="identical blocked detail",
            consecutive_conflicts=0,
        ).set_intervention_required("identical blocked detail"),
    )

    for _ in range(cap):
        state = run_loop._wait_for_integration_retry(state, ctx)
    assert sleeps == [], "Identical blocked outcomes must not sleep"
    assert len(handoffs) == 1
    assert state.rebase.is_intervention_required is True
    assert state.rebase.consecutive_conflicts >= cap


def test_wait_for_retry_unsuccessful_handoff_records_intervention_without_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-2 unsuccessful handoff: handoff that does not progress still escalates."""
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)
    handoff_calls: list[str] = []
    monkeypatch.setattr(
        run_loop, "_handoff_retained_failure", lambda _c, d: handoff_calls.append(d)
    )

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="unsuccessful handoff detail",
            consecutive_conflicts=cap,
        ).set_intervention_required("unsuccessful handoff detail"),
    )

    result = run_loop._wait_for_integration_retry(state, ctx)
    assert sleeps == [], "Unsuccessful handoff must not sleep"
    assert len(handoff_calls) == 1
    assert "unsuccessful handoff detail" in handoff_calls[0]
    assert result.rebase.is_intervention_required is True


def test_wait_for_retry_pending_repeated_identical_blocked_outcomes_progress_and_escalate(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-2 pending retry: repeated non-None blocked results charge attempts and escalate."""
    _sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    handoffs: list[str] = []
    monkeypatch.setattr(run_loop, "_handoff_retained_failure", lambda _c, d: handoffs.append(d))

    def _fake_preamble(
        _scope: WorkspaceScope,
        _config: UnifiedConfig,
        *,
        backend: object = None,
    ) -> RebaseState | None:
        return RebaseState(
            last_action="skipped",
            last_reason="integration worktree busy; recovery will retry",
            recovery_record_retained=True,
        )

    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _fake_preamble)

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="pending retry detail",
            consecutive_conflicts=0,
        ),
    )
    assert state.rebase.is_intervention_required is False

    for _ in range(cap):
        state = run_loop._wait_for_integration_retry(state, ctx)

    assert len(handoffs) == 1
    assert state.rebase.is_intervention_required is True
    assert state.rebase.consecutive_conflicts >= cap


def test_wait_for_retry_successful_handoff_not_overwritten_with_intervention(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-2 successful handoff: does not overwrite a successful handoff with intervention."""
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _mock_successful_handoff)

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    initial_state = _make_state(conflicts=cap, last_reason="conflict detail for resolver")
    ctx.latest_state[:] = [initial_state]

    result = run_loop._wait_for_integration_retry(initial_state, ctx)
    assert sleeps == []
    assert result.rebase.is_intervention_required is False
    assert result.rebase.last_outcome == RebaseOutcomeKind.COMPLETED


def test_wait_for_retry_pending_successful_handoff_not_overwritten_with_intervention(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-2 pending retry: successful handoff at cap does not overwrite with intervention."""
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _mock_successful_handoff)

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="pending retry detail",
            consecutive_conflicts=cap - 1,
        ),
    )
    ctx.latest_state[:] = [initial_state]

    result = run_loop._wait_for_integration_retry(initial_state, ctx)
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
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-2 absent record: absent record at cap invokes automated resolver or re-derives state."""
    _sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    resolver_called: list[bool] = []

    def _mock_resolver(_c: run_loop._LoopContext, _r: RebaseState | None = None) -> bool:
        resolver_called.append(True)
        return False

    monkeypatch.setattr(run_loop, "_run_integration_conflict_resolution", _mock_resolver)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _mock_rederived_preamble)

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    initial_state = _make_state(conflicts=cap)
    ctx.latest_state[:] = [initial_state]

    result = run_loop._wait_for_integration_retry(initial_state, ctx)
    assert resolver_called == [True], "Automated resolver must be invoked for absent record at cap"
    assert result.rebase.is_intervention_required is False
    assert result.rebase.last_outcome == RebaseOutcomeKind.COMPLETED


def test_wait_for_retry_unreadable_record_at_cap_invokes_automated_resolver_or_rederives_state(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-2 unreadable record: unreadable record at cap invokes automated resolver or re-derives state."""
    _sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    resolver_called: list[bool] = []

    def _mock_resolver(_c: run_loop._LoopContext, _r: RebaseState | None = None) -> bool:
        resolver_called.append(True)
        return False

    monkeypatch.setattr(run_loop, "_run_integration_conflict_resolution", _mock_resolver)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _mock_rederived_preamble)

    target_file = record_path(tmp_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("CORRUPT_JSON_{invalid")

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    initial_state = _make_state(conflicts=cap)
    ctx.latest_state[:] = [initial_state]

    result = run_loop._wait_for_integration_retry(initial_state, ctx)
    assert resolver_called == [True], (
        "Automated resolver must be invoked for unreadable record at cap"
    )
    assert result.rebase.is_intervention_required is False
    assert result.rebase.last_outcome == RebaseOutcomeKind.COMPLETED


def test_wait_for_retry_unreadable_record_at_cap_unsuccessful_escalates_to_intervention(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-2 unreadable record: unreadable record at cap escalates to intervention if unresolved."""
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    monkeypatch.setattr(run_loop, "_run_integration_conflict_resolution", lambda _c, _r=None: False)

    target_file = record_path(tmp_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("CORRUPT_JSON_{invalid")

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    initial_state = _make_state(conflicts=cap)
    ctx.latest_state[:] = [initial_state]

    result = run_loop._wait_for_integration_retry(initial_state, ctx)
    assert sleeps == []
    assert result.rebase.is_intervention_required is True


def test_wait_for_retry_cold_durable_resolution_exhausted_with_live_conflict_dispatches_session_handoff(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
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
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    assert sleeps == []

    handoff_calls: list[str] = []

    def _fake_handoff(loop_ctx: object, detail: str) -> None:
        handoff_calls.append(detail)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _fake_handoff)

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
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

    first_result = run_loop._wait_for_integration_retry(initial_state, ctx)
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
    # 4. Intervention is recorded (handoff did not clear exhaustion in this mock).
    assert first_result.rebase.is_intervention_required is True

    # 5. A second call within the SAME session MUST NOT re-dispatch the handoff.
    second_result = run_loop._wait_for_integration_retry(first_result, ctx)
    assert len(handoff_calls) == 1, (
        "Same-session calls must not re-dispatch the automated resolver handoff "
        "(O1 cap-bound escalation, no operator-visible rotation)."
    )
    assert second_result.rebase.is_intervention_required is True
    assert sleeps == [], "Repeated retry calls must not sleep"


def test_wait_for_retry_cold_exhausted_state_with_no_live_conflict_still_dispatches_session_handoff(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
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
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

    handoff_calls: list[str] = []

    monkeypatch.setattr(
        run_loop, "_handoff_retained_failure", lambda _c, d: handoff_calls.append(d)
    )

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
        < ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    )

    first_result = run_loop._wait_for_integration_retry(initial_state, ctx)
    assert len(handoff_calls) == 1
    assert sleeps == []
    assert first_result.rebase.is_intervention_required is True

    run_loop._wait_for_integration_retry(first_result, ctx)
    assert len(handoff_calls) == 1, "Same-session calls must not re-dispatch handoff"


def test_wait_for_retry_prolonged_unreachable_remote_does_not_increment_conflicts_or_set_intervention(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """WU-1 O1/O2/AC8: prolonged unreachable remote does not trip conflict cap or intervention."""
    from ralph.pipeline._publication_state_machine import PublicationDecision

    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)
    cap = 2
    monkeypatch.setattr(run_loop, "_effective_conflict_cap", lambda _c: cap)

    outage_recovered = RebaseState(
        last_reason="remote tacos unreachable; recovery will retry",
        last_publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        consecutive_conflicts=0,
    )
    monkeypatch.setattr(
        run_loop,
        "_run_auto_integrate_recovery_preamble",
        lambda _s, _c, backend=None: outage_recovered,
    )

    state = PipelineState(
        phase="development",
        rebase=outage_recovered,
    )
    ctx.latest_state[:] = [state]

    # Retry cap + 2 times (4 times, > cap of 2)
    for _ in range(cap + 2):
        state = run_loop._wait_for_integration_retry(state, ctx)
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
    monkeypatch.setattr(
        run_loop,
        "_run_auto_integrate_recovery_preamble",
        lambda _s, _c, backend=None: restored_recovered,
    )

    final_state = run_loop._wait_for_integration_retry(state, ctx)
    assert final_state.rebase.is_intervention_required is False
    assert final_state.rebase.consecutive_conflicts == 0
    assert final_state.rebase.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED


def test_wait_for_retry_successful_cold_exhaustion_handoff_clears_state(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
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
    sleeps, ctx = _setup_retry_env(tmp_path, monkeypatch)

    handoff_calls: list[str] = []

    # A mock handoff that successfully resolves the durable record
    # on the first try. Mirrors the contract of an automated
    # resolver that succeeds (e.g. a successful git rebase
    # followed by a clean push).
    def _clearing_handoff(loop_ctx: run_loop._LoopContext, detail: str) -> None:
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

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _clearing_handoff)

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
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

    result = run_loop._wait_for_integration_retry(initial_state, ctx)

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
) -> tuple[list[float], run_loop._LoopContext, dict[str, int]]:
    """Set up a minimal context with a low conflict cap so cold-cap is reachable."""
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    # The probe uses cap=5; override the effective cap so
    # ``resolver_attempt_limit`` returns the value the analysis
    # feedback probed with (and so consecutive_conflicts=5 is
    # at the cap).
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.resolver_attempt_limit",
        lambda _c: cap,
    )
    # The integration target resolution requires a real local branch
    # (it consults ``observe_branch_sha``), which is unavailable in a
    # ``tmp_path`` workspace. Stub it so the seam gets a valid target.
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target",
        lambda _cfg, _root: target,
    )
    ctx._cold_capped_escalation_attempted = False
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
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
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

    monkeypatch.setattr(run_loop, "_paused_rebase_at", lambda _r: False)
    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _fake_build)
    monkeypatch.setattr(run_loop, "_complete_in_progress_merge", _fake_merge)
    monkeypatch.setattr(
        run_loop,
        "_resolve_orphaned_unmerged_index",
        lambda _c, _t: counters.__setitem__("orphan", counters["orphan"] + 1) or False,
    )
    monkeypatch.setattr(
        run_loop,
        "_rederive_live_conflict_state",
        lambda _r, _t, _rb: counters.__setitem__("rederive", counters["rederive"] + 1) or None,
    )

    cold_capped = RebaseState(
        last_action="conflict",
        last_target="release",
        consecutive_conflicts=5,  # == cap
    ).model_copy(update={"resolution_exhausted": True})

    result = run_loop._run_owned_integration_conflict_resolution(ctx, cold_capped)

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
    assert ctx._cold_capped_escalation_attempted is True


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
    from ralph.pipeline._integration_action import RebaseOutcomeKind
    from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity

    _sleeps, ctx, counters = _make_cold_capped_ctx(tmp_path, monkeypatch, cap=5)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_budget_seam.observe_conflict_identity",
        lambda _root, _target, **_k: ConflictIdentity(),
        raising=False,
    )

    monkeypatch.setattr(run_loop, "_paused_rebase_at", lambda _r: False)

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

    monkeypatch.setattr(run_loop, "_rederive_live_conflict_state", _fake_rederive)

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

    result = run_loop._run_owned_integration_conflict_resolution(ctx, cold_capped)

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
    assert ctx._cold_capped_escalation_attempted is True
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

    monkeypatch.setattr(run_loop, "_paused_rebase_at", lambda _r: False)

    def _fake_build(**_kwargs: object) -> object:
        counters["built"] += 1
        return lambda _root, _target: False

    def _fake_merge(_root: object, _target: object, _resolver: object) -> bool:
        counters["merge"] += 1
        return False

    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _fake_build)
    monkeypatch.setattr(run_loop, "_complete_in_progress_merge", _fake_merge)
    monkeypatch.setattr(run_loop, "_resolve_orphaned_unmerged_index", lambda _c, _t: False)
    monkeypatch.setattr(run_loop, "_rederive_live_conflict_state", lambda _r, _t, _rb: None)

    cold_capped = RebaseState(
        last_action="conflict",
        last_target="release",
        consecutive_conflicts=5,
    ).model_copy(update={"resolution_exhausted": True})
    initial_counter = cold_capped.consecutive_conflicts

    result = run_loop._run_owned_integration_conflict_resolution(ctx, cold_capped)

    # 1. The resolver was built and run exactly once.
    assert counters["built"] == 1
    assert counters["merge"] == 1
    # 2. The function returned False (escalation did not clear the state).
    assert result is False
    # 3. The session flag is set so a same-session repeat call does not spin.
    assert ctx._cold_capped_escalation_attempted is True
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

    monkeypatch.setattr(run_loop, "_paused_rebase_at", lambda _r: False)

    def _fake_build(**_kwargs: object) -> object:
        counters["built"] += 1
        return lambda _root, _target: False

    def _fake_merge(_root: object, _target: object, _resolver: object) -> bool:
        counters["merge"] += 1
        return False

    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _fake_build)
    monkeypatch.setattr(run_loop, "_complete_in_progress_merge", _fake_merge)
    monkeypatch.setattr(run_loop, "_resolve_orphaned_unmerged_index", lambda _c, _t: False)
    monkeypatch.setattr(run_loop, "_rederive_live_conflict_state", lambda _r, _t, _rb: None)

    cold_capped = RebaseState(
        last_action="conflict",
        last_target="release",
        consecutive_conflicts=5,
    ).model_copy(update={"resolution_exhausted": True})

    # First call: escalation runs.
    first = run_loop._run_owned_integration_conflict_resolution(ctx, cold_capped)
    assert first is False
    assert counters["built"] == 1
    # Second call within the SAME session: escalation skipped, budget gate fires.
    second = run_loop._run_owned_integration_conflict_resolution(ctx, cold_capped)
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

    monkeypatch.setattr(run_loop, "_paused_rebase_at", lambda _r: False)

    def _fake_build(**_kwargs: object) -> object:
        counters["built"] += 1
        return lambda _root, _target: True

    def _fake_merge(_root: object, _target: object, _resolver: object) -> bool:
        counters["merge"] += 1
        return True

    monkeypatch.setattr(run_loop, "build_agent_conflict_resolver", _fake_build)
    monkeypatch.setattr(run_loop, "_complete_in_progress_merge", _fake_merge)
    monkeypatch.setattr(run_loop, "_resolve_orphaned_unmerged_index", lambda _c, _t: False)
    monkeypatch.setattr(run_loop, "_rederive_live_conflict_state", lambda _r, _t, _rb: None)

    spent_on_another_conflict = RebaseState(
        last_action="conflict",
        last_target="release",
        consecutive_conflicts=5,
        last_conflict_feature_sha="feature-1",
        last_conflict_target_sha="target-1",
        last_conflict_scope="feature",
    ).model_copy(update={"resolution_exhausted": True})

    result = run_loop._run_owned_integration_conflict_resolution(ctx, spent_on_another_conflict)

    # 1. The resolver ran via the ordinary path (the cold-capped escalation
    # must NOT have fired).
    assert counters["built"] == 1, (
        f"Ordinary path must run for a different-conflict identity; built={counters['built']}"
    )
    assert result is True
    # 2. The cold-capped flag was NOT set (the escalation did not fire).
    assert ctx._cold_capped_escalation_attempted is False, (
        "Cold-capped escalation must not fire for a different-conflict "
        "identity; the budget is per-identity and the new conflict "
        "inherits a fresh budget via resolver_allowed"
    )
