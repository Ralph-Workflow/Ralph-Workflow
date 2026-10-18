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
                pipeline=PipelinePolicy(
                    entry_phase="development",
                    phases={
                        "development": PhaseDefinition(
                            drain="development",
                            transitions=PhaseTransition(on_success="complete"),
                        )
                    },
                )
            ),
            active_display=SimpleNamespace(emit_warn_line=_noop_warn),
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(root),
            latest_state=latest_state,
            sleep=sleep_sink.append,
            connectivity_monitor=SimpleNamespace(current_state=None),
            _session_handoff_dispatched=False,
        ),
    )


def test_wait_for_retry_unchanged_durable_record_clears_evidence_without_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Requirement 2(a): Matching / UNCHANGED durable record.

    Asserts that _wait_for_integration_retry does not call ctx.sleep,
    durable evidence is cleared from disk, and it returns immediately.
    """
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

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

    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="synchronization unproven",
        ),
    )

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
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

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

    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="synchronization unproven",
        ),
    )

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
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)

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

    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="synchronization unproven",
        ),
    )

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
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)

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

    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="synchronization unproven",
        ),
    )

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
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

    # Write corrupt payload to the record path
    target_file = record_path(tmp_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("CORRUPT_JSON_{not valid")

    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="synchronization unproven",
        ),
    )

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
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

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
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

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

    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="synchronization unproven",
        ),
    )

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
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    preamble_called: list[bool] = []

    def _recording_preamble(
        _scope: WorkspaceScope,
        _config: UnifiedConfig,
        *,
        backend: object = None,
    ) -> RebaseState | None:
        preamble_called.append(True)
        return None

    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
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

    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="synchronization unproven",
        ),
    )

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
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

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

    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="synchronization unproven",
        ),
    )

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

    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

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

    initial_state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_action="conflict",
            last_reason="synchronization unproven",
        ),
    )

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

    def _recording_save(state: PipelineState, _c: run_loop._LoopContext) -> None:
        saved_checkpoints.append(state)

    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _recording_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

    handoffs: list[str] = []

    def _recording_handoff(_c: object, failure: str) -> None:
        handoffs.append(failure)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _recording_handoff)

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
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)
    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

    handoff_calls: list[str] = []

    def _fake_handoff(loop_ctx: object, detail: str) -> None:
        handoff_calls.append(detail)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _fake_handoff)

    cap = ctx.config.conflict_resolution.max_consecutive_resolver_attempts
    # COLD-LOADED: intervention-required AND consecutive-conflicts at cap
    cold_loaded_rebase = RebaseState(
        last_action="conflict",
        last_reason="cold-loaded detail",
        consecutive_conflicts=cap,
    ).set_intervention_required("cold-loaded detail")
    initial_state = PipelineState(
        phase="development",
        rebase=cold_loaded_rebase,
    )
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
    """WU-2 retry progress: identical blocked outcomes charge attempts and escalate.

    When the recovery preamble returns a non-None state that is itself
    blocked, repeated calls must charge the conflict counter, reach the
    cap, escalate to the resolver handoff, and never sleep.
    """
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)

    handoffs: list[str] = []

    def _fake_handoff(loop_ctx: object, detail: str) -> None:
        handoffs.append(detail)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _fake_handoff)

    def _fake_preamble(
        _scope: WorkspaceScope,
        _config: UnifiedConfig,
        *,
        backend: object = None,
    ) -> RebaseState | None:
        # Returns a non-None blocked state (is_intervention_required=True).
        # Mirrors an unchanging durable state that nothing in-process can satisfy.
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

    # Repeated iterations must charge attempts and eventually escalate.
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
    """WU-2 unsuccessful handoff: handoff that does not progress still escalates.

    When the automated-resolution handoff does not produce observable
    progress (the in-memory rebase still carries
    ``is_intervention_required``), the retry must record the
    intervention transition without sleeping so the next seam can
    re-derive state from Git instead of looping forever on a fixed
    interval wait.
    """
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)

    def _noop_preamble(
        _scope: WorkspaceScope,
        _config: UnifiedConfig,
        *,
        backend: object = None,
    ) -> RebaseState | None:
        return None

    monkeypatch.setattr(run_loop, "_run_auto_integrate_recovery_preamble", _noop_preamble)

    handoff_calls: list[str] = []

    def _noop_handoff(loop_ctx: object, detail: str) -> None:
        # Simulates a handoff that does NOT make progress (no observable
        # side-effect on the rebase state).
        handoff_calls.append(detail)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _noop_handoff)

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
    """WU-2 pending retry: repeated non-None blocked results charge attempts and escalate.

    When starting from a pending (non-intervention-required) state and
    the recovery preamble repeatedly returns an identical non-None
    blocked state, the retry loop must charge consecutive conflicts on
    each iteration, reach the cap, dispatch automated resolution handoff,
    and escalate without sleeping.
    """
    sleeps: list[float] = []
    ctx = _create_retry_ctx(tmp_path, sleeps)
    monkeypatch.setattr(run_loop, "_save_recovered_rebase_checkpoint", _noop_save)

    handoffs: list[str] = []

    def _fake_handoff(loop_ctx: object, detail: str) -> None:
        handoffs.append(detail)

    monkeypatch.setattr(run_loop, "_handoff_retained_failure", _fake_handoff)

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
