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


def test_wait_for_retry_unreadable_record_escalates_to_intervention_with_cooldown_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Requirement 2(c): Unreadable record.

    Asserts that _wait_for_integration_retry escalates to INTERVENTION_REQUIRED
    and performs the standard cooldown retry wait when the durable record file is unreadable.
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

    # 1. Assert ctx.sleep was called for cooldown retry
    assert sleeps == [5.0], "Unreadable record must perform standard cooldown wait"

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


def test_wait_for_retry_empty_evidence_durable_record_escalates_to_intervention_with_cooldown_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Requirement: Empty evidence record escalates with cooldown sleep."""
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

    assert sleeps == [5.0], "Empty evidence record must perform standard cooldown wait"
    assert updated_state.rebase.is_intervention_required is True
    assert updated_state.rebase.last_outcome in (
        RebaseOutcomeKind.INTERVENTION_REQUIRED,
        RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
    )


def test_wait_for_retry_negative_no_intervention_required_falls_through_to_sleep(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Requirement 2(d): Negative test - record with no intervention required.

    Asserts that when diagnostic_intervention_required is False,
    _wait_for_integration_retry falls through to the existing 5.0s retry sleep.
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

    run_loop._wait_for_integration_retry(initial_state, ctx)

    # 1. Assert ctx.sleep(5.0) WAS called
    assert sleeps == [5.0], "Negative test must fall through to 5.0s sleep"

    # 2. Assert recovery preamble was invoked
    assert preamble_called == [True], "Recovery preamble should be invoked after sleep"
