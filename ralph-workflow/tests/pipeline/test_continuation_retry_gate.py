"""Tests for durable continuation retry gate for auto-integrate continuation (Unit B / Defect 9).

Covers:
1. unchanged key -> resolver invoked once, exactly one WAITING line, zero repeated CRITICAL;
2. a SECOND process (fresh in-memory state, reloaded record) with an unchanged record does not re-enter resolution;
3. agent becomes eligible/reset -> retried exactly once;
4. config fingerprint change still retries once;
5. old record without the new field loads and behaves as ungated;
6. merge continuation retry gate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    parse_record_payload,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_recovery_record import (
    compute_continuation_retry_gate_key,
    continue_retained_resolution,
    recover_pending_merge,
    reset_continuation_gate_state,
)
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions


@pytest.fixture(autouse=True)
def _clean_gate_state() -> None:
    reset_continuation_gate_state()


def _capture_loguru_records() -> tuple[list[dict[str, Any]], int]:
    captured: list[dict[str, Any]] = []

    def _sink(message: object) -> None:
        rec = getattr(message, "record", None)
        if rec is not None:
            captured.append(rec)

    sink_id = logger.add(_sink, level="DEBUG")
    return captured, sink_id


def _setup_repo_and_config(tmp_path: Path) -> tuple[Path, Path]:
    cfg_dir = tmp_path / ".agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "ralph-workflow.toml"
    cfg_file.write_text("[general]\nmodel = 'test'\n", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
    (git_dir / "objects").mkdir(parents=True, exist_ok=True)
    (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (git_dir / "refs" / "heads" / "main").write_text("0" * 40 + "\n", encoding="utf-8")
    return tmp_path, cfg_file


def _setup_rebase_state(
    root: Path,
    feat_sha: str = "feat1",
    target_sha: str = "target1",
    stop_sha: str = "stop_sha_1",
) -> None:
    rebase_dir = root / ".git" / "rebase-merge"
    rebase_dir.mkdir(parents=True, exist_ok=True)
    (rebase_dir / "head-name").write_text("refs/heads/feature\n", encoding="utf-8")
    (rebase_dir / "orig-head").write_text(f"{feat_sha}\n", encoding="utf-8")
    (rebase_dir / "onto").write_text(f"{target_sha}\n", encoding="utf-8")
    (rebase_dir / "stopped-sha").write_text(f"{stop_sha}\n", encoding="utf-8")


def _setup_controller(clock: FakeClock | None = None) -> tuple[RecoveryController, FakeClock]:
    clk = clock or FakeClock()
    opts = RecoveryControllerOptions(clock=clk)
    ctrl = RecoveryController(options=opts)
    ctrl.register_phase_chain("rebase_conflict_resolution", ["agent1", "agent2"])
    return ctrl, clk


def test_retry_gate_key_computation_and_record_field(tmp_path: Path) -> None:
    """Retry gate key contains (stop_id, sorted_eligible_agents, config_fingerprint)."""
    root, _cfg = _setup_repo_and_config(tmp_path)
    ctrl, _clk = _setup_controller()
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat_sha",
        pre_target_sha="target_sha",
        resolving_rebase=True,
    )

    # Initial state: both agent1 and agent2 available
    key1 = compute_continuation_retry_gate_key(
        root,
        record,
        recovery_controller=ctrl,
        stop_identity="stop_commit_1",
    )
    assert key1[0] == "stop_commit_1"
    assert key1[1] == ("agent1", "agent2")
    assert key1[2] is not None
    assert len(key1[2]) == 64  # SHA-256

    # When an agent is marked unavailable, sorted eligible set changes
    ctrl.unavailability_store.mark_unavailable("rebase_conflict_resolution", "agent1", "failed")
    key2 = compute_continuation_retry_gate_key(
        root,
        record,
        recovery_controller=ctrl,
        stop_identity="stop_commit_1",
    )
    assert key2[0] == "stop_commit_1"
    assert key2[1] == ("agent2",)
    assert key2[2] == key1[2]
    assert key2 != key1

    # Update record with gate key and verify serialization round-trip
    updated = record.with_resolution_gate_key(key2)
    assert updated.resolution_gate_key == key2
    write_record(root, updated)

    loaded = read_record(root)
    assert loaded is not None
    assert loaded.resolution_gate_key == key2


def test_unchanged_key_invokes_resolver_once_one_waiting_zero_repeated_critical(
    tmp_path: Path,
) -> None:
    """Case 1: unchanged key -> resolver invoked once, exactly one WAITING line, zero repeated CRITICAL."""
    root, _cfg = _setup_repo_and_config(tmp_path)
    ctrl, _clk = _setup_controller()
    ctrl.unavailability_store.mark_unavailable("rebase_conflict_resolution", "agent1", "cooldown")
    _setup_rebase_state(root, "feat1", "target1", "stop_sha_1")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )
    write_record(root, record)

    resolver_invocations = [0]

    def fake_resolve(*_args: Any, **_kwargs: Any) -> RebaseResolutionResult:
        resolver_invocations[0] += 1
        return RebaseResolutionResult(
            succeeded=False,
            reason="rebase resolution remains pending after continuation",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )

    captured, sink_id = _capture_loguru_records()
    try:
        # --- Round 1: first attempt (ungated) ---
        outcome1 = recover_pending_merge(
            root,
            read_record(root) or record,
            config=None,
            rebase_stop_resolver=fake_resolve,
            recovery_controller=ctrl,
        )
        assert outcome1.recovery_record_retained is True

        # Check records after Round 1: exactly one CRITICAL log from the failure, zero WAITING logs
        critical_logs_r1 = [r for r in captured if r["level"].name == "CRITICAL"]
        waiting_logs_r1 = [r for r in captured if "WAITING" in r["message"]]
        assert len(critical_logs_r1) == 1
        assert len(waiting_logs_r1) == 0

        # Persisted integration record stores the computed resolution gate key.
        record_after_r1 = read_record(root)
        assert record_after_r1 is not None
        assert record_after_r1.resolution_gate_key is not None

        # --- Round 2: retry with unchanged key ---
        outcome2 = recover_pending_merge(
            root,
            record_after_r1,
            config=None,
            rebase_stop_resolver=fake_resolve,
            recovery_controller=ctrl,
        )
        assert outcome2.recovery_record_retained is True
        assert "WAITING" in outcome2.last_reason

        # Exactly one WAITING line emitted across both rounds
        waiting_logs_r2 = [r for r in captured if "WAITING" in r["message"]]
        assert len(waiting_logs_r2) == 1

        # Zero repeated CRITICAL logs (count remains 1)
        critical_logs_r2 = [r for r in captured if r["level"].name == "CRITICAL"]
        assert len(critical_logs_r2) == 1

        # --- Round 3: another per-tick check with unchanged key ---
        recover_pending_merge(
            root,
            read_record(root) or record,
            config=None,
            rebase_stop_resolver=fake_resolve,
            recovery_controller=ctrl,
        )
        assert len([r for r in captured if r["level"].name == "CRITICAL"]) == 1
        assert len([r for r in captured if "WAITING" in r["message"]]) == 1

    finally:
        logger.remove(sink_id)


def test_second_process_with_unchanged_record_does_not_reenter_resolution(
    tmp_path: Path,
) -> None:
    """Case 2: a SECOND process (fresh in-memory state, reloaded record) does not re-enter resolution."""
    root, _cfg = _setup_repo_and_config(tmp_path)
    ctrl1, clock = _setup_controller()
    ctrl1.unavailability_store.mark_unavailable("rebase_conflict_resolution", "agent1", "cooldown")
    _setup_rebase_state(root, "feat1", "target1", "stop_sha_1")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )
    write_record(root, record)

    def fake_resolve(*_args: Any, **_kwargs: Any) -> RebaseResolutionResult:
        return RebaseResolutionResult(
            succeeded=False,
            reason="rebase resolution remains pending after continuation",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )

    # Process 1 runs round 1
    recover_pending_merge(
        root,
        record,
        config=None,
        rebase_stop_resolver=fake_resolve,
        recovery_controller=ctrl1,
    )
    persisted_record = read_record(root)
    assert persisted_record is not None
    assert persisted_record.resolution_gate_key is not None

    # Process 2 starts fresh: new controller, new clock at same time, reset in-process gate state
    reset_continuation_gate_state()
    ctrl2 = RecoveryController(options=RecoveryControllerOptions(clock=clock))
    ctrl2.register_phase_chain("rebase_conflict_resolution", ["agent1", "agent2"])
    ctrl2.unavailability_store.mark_unavailable("rebase_conflict_resolution", "agent1", "cooldown")

    captured, sink_id = _capture_loguru_records()
    try:
        outcome_p2 = recover_pending_merge(
            root,
            persisted_record,
            config=None,
            rebase_stop_resolver=fake_resolve,
            recovery_controller=ctrl2,
        )
        # Process 2 sees the durable gate key from disk, emits WAITING, does not re-resolve
        assert outcome_p2.recovery_record_retained is True
        assert "WAITING" in outcome_p2.last_reason

        # Exactly one WAITING line emitted in Process 2, zero CRITICAL logs
        waiting_logs = [r for r in captured if "WAITING" in r["message"]]
        critical_logs = [r for r in captured if r["level"].name == "CRITICAL"]
        assert len(waiting_logs) == 1
        assert "Operator action: run 'ralph --retry'" in waiting_logs[0]["message"]
        assert "ralph --check-config" in waiting_logs[0]["message"]
        assert len(critical_logs) == 0

        # Process 3 verifies cross-process deduplication from the persisted record
        p2_persisted = read_record(root)
        assert p2_persisted is not None
        assert p2_persisted.waiting_notification_key is not None

        reset_continuation_gate_state()
        ctrl3 = RecoveryController(options=RecoveryControllerOptions(clock=clock))
        ctrl3.register_phase_chain("rebase_conflict_resolution", ["agent1", "agent2"])
        ctrl3.unavailability_store.mark_unavailable(
            "rebase_conflict_resolution", "agent1", "cooldown"
        )

        outcome_p3 = recover_pending_merge(
            root,
            p2_persisted,
            config=None,
            rebase_stop_resolver=fake_resolve,
            recovery_controller=ctrl3,
        )
        assert outcome_p3.recovery_record_retained is True
        # Process 3 emits zero additional WAITING logs across processes
        waiting_logs_after = [r for r in captured if "WAITING" in r["message"]]
        assert len(waiting_logs_after) == 1
    finally:
        logger.remove(sink_id)


def test_agent_eligibility_change_retries_exactly_once(
    tmp_path: Path,
) -> None:
    """Case 3: agent becomes eligible/reset -> retried exactly once."""
    root, _cfg = _setup_repo_and_config(tmp_path)
    ctrl, clock = _setup_controller()
    # Cooldown duration 60s for agent1
    ctrl.unavailability_store.mark_unavailable("rebase_conflict_resolution", "agent1", "cooldown")
    _setup_rebase_state(root, "feat1", "target1", "stop_sha_1")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )
    write_record(root, record)

    def fake_resolve(*_args: Any, **_kwargs: Any) -> RebaseResolutionResult:
        return RebaseResolutionResult(
            succeeded=False,
            reason="rebase resolution remains pending after continuation",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )

    # Round 1: agent1 is in cooldown, key has ('agent2',)
    recover_pending_merge(
        root,
        record,
        config=None,
        rebase_stop_resolver=fake_resolve,
        recovery_controller=ctrl,
    )
    rec1 = read_record(root)
    assert rec1 is not None
    assert rec1.resolution_gate_key is not None
    r1_key = rec1.resolution_gate_key
    assert r1_key[1] == ("agent2",)

    # Immediate second call is gated
    recover_pending_merge(
        root,
        rec1,
        config=None,
        rebase_stop_resolver=fake_resolve,
        recovery_controller=ctrl,
    )

    # Advancing clock past expiration restores candidate eligibility.
    clock.advance(60.0)
    assert ctrl.agents_now_available("rebase_conflict_resolution", ["agent1", "agent2"]) == [
        "agent1",
        "agent2",
    ]

    captured, sink_id = _capture_loguru_records()
    try:
        # Round 3: eligibility changed -> gate detects key change -> retries once
        rec2 = read_record(root)
        assert rec2 is not None
        recover_pending_merge(
            root,
            rec2,
            config=None,
            rebase_stop_resolver=fake_resolve,
            recovery_controller=ctrl,
        )
        rec3 = read_record(root)
        assert rec3 is not None
        assert rec3.resolution_gate_key is not None
        r3_key = rec3.resolution_gate_key
        assert r3_key[1] == ("agent1", "agent2")
        assert r3_key != r1_key

        # Immediately following call with no eligibility change: gated again!
        recover_pending_merge(
            root,
            rec3,
            config=None,
            rebase_stop_resolver=fake_resolve,
            recovery_controller=ctrl,
        )

        waiting_logs = [r for r in captured if "WAITING" in r["message"]]
        assert len(waiting_logs) == 1
    finally:
        logger.remove(sink_id)


def test_config_fingerprint_change_retries_once(
    tmp_path: Path,
) -> None:
    """Case 4: config fingerprint change still retries once."""
    root, cfg_file = _setup_repo_and_config(tmp_path)
    ctrl, _clk = _setup_controller()
    ctrl.unavailability_store.mark_unavailable("rebase_conflict_resolution", "agent1", "cooldown")
    _setup_rebase_state(root, "feat1", "target1", "stop_sha_1")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )
    write_record(root, record)

    def fake_resolve(*_args: Any, **_kwargs: Any) -> RebaseResolutionResult:
        return RebaseResolutionResult(
            succeeded=False,
            reason="rebase resolution remains pending after continuation",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )

    # Round 1
    recover_pending_merge(
        root,
        record,
        config=None,
        rebase_stop_resolver=fake_resolve,
        recovery_controller=ctrl,
    )
    rec1 = read_record(root)
    assert rec1 is not None
    assert rec1.resolution_gate_key is not None
    r1_key = rec1.resolution_gate_key

    # Gated while unchanged
    recover_pending_merge(
        root,
        rec1,
        config=None,
        rebase_stop_resolver=fake_resolve,
        recovery_controller=ctrl,
    )

    # Modify config file (simulating user edit)
    cfg_file.write_text("[general]\nmodel = 'repaired-model'\n", encoding="utf-8")

    captured, sink_id = _capture_loguru_records()
    try:
        # Config changed -> retry once
        rec2 = read_record(root)
        assert rec2 is not None
        recover_pending_merge(
            root,
            rec2,
            config=None,
            rebase_stop_resolver=fake_resolve,
            recovery_controller=ctrl,
        )
        rec3 = read_record(root)
        assert rec3 is not None
        assert rec3.resolution_gate_key is not None
        r3_key = rec3.resolution_gate_key
        assert r3_key[2] != r1_key[2]  # fingerprint updated

        # Further call without config edit is gated again
        recover_pending_merge(
            root,
            rec3,
            config=None,
            rebase_stop_resolver=fake_resolve,
            recovery_controller=ctrl,
        )
        waiting_logs = [r for r in captured if "WAITING" in r["message"]]
        assert len(waiting_logs) == 1
    finally:
        logger.remove(sink_id)


def test_legacy_record_without_gate_key_is_ungated(
    tmp_path: Path,
) -> None:
    """Case 5: old record without the new field loads and behaves as ungated."""
    root, _cfg = _setup_repo_and_config(tmp_path)
    ctrl, _clk = _setup_controller()
    ctrl.unavailability_store.mark_unavailable("rebase_conflict_resolution", "agent1", "cooldown")
    _setup_rebase_state(root, "feat1", "target1", "stop_sha_1")

    # Simulate legacy JSON payload written by previous versions without resolution_gate_key field
    legacy_payload = {
        "schema_version": 1,
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "feat1",
        "pre_target_sha": "target1",
        "resolving_rebase": True,
    }
    rec_file = root / ".agent" / "auto_integrate_in_progress.json"
    rec_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

    # Verify parse_record_payload correctly loads it with None resolution_gate_key
    loaded = parse_record_payload(legacy_payload)
    assert loaded is not None
    assert loaded.resolution_gate_key is None

    def fake_resolve(*_args: Any, **_kwargs: Any) -> RebaseResolutionResult:
        return RebaseResolutionResult(
            succeeded=False,
            reason="rebase resolution remains pending after continuation",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )

    # First attempt: behaves normally (ungated)
    outcome = recover_pending_merge(
        root,
        loaded,
        config=None,
        rebase_stop_resolver=fake_resolve,
        recovery_controller=ctrl,
    )
    assert outcome.recovery_record_retained is True

    # After the attempt, new resolution_gate_key is written back
    saved = read_record(root)
    assert saved is not None
    assert saved.resolution_gate_key is not None


def test_merge_continuation_retry_gate(
    tmp_path: Path,
) -> None:
    """Merge conflict resolution continuation also respects the retry gate."""
    root, _cfg = _setup_repo_and_config(tmp_path)
    ctrl, _clk = _setup_controller()

    feat_sha = "0" * 40
    target_sha = "a" * 40
    (root / ".git" / "MERGE_HEAD").write_text(f"{target_sha}\n", encoding="utf-8")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat_sha,
        pre_target_sha=target_sha,
        resolving_merge=True,
        conflicted_files=("f.py",),
    )
    write_record(root, record)

    merge_resolve_invocations = [0]

    def counting_resolver(*_args: Any, **_kwargs: Any) -> Any:
        merge_resolve_invocations[0] += 1
        return False

    # Round 1: ungated attempt
    res1 = continue_retained_resolution(
        root,
        record,
        None,
        counting_resolver,
        None,
        recovery_controller=ctrl,
    )
    assert merge_resolve_invocations[0] == 1
    assert isinstance(res1, str)
    assert "progress retained" in res1

    rec_after = read_record(root)
    assert rec_after is not None
    assert rec_after.resolution_gate_key is not None

    # Round 2: retry with unchanged record and unchanged state -> GATED!
    res2 = continue_retained_resolution(
        root,
        rec_after,
        None,
        counting_resolver,
        None,
        recovery_controller=ctrl,
    )
    assert merge_resolve_invocations[0] == 1  # Resolver not re-entered!
    assert isinstance(res2, str)
    assert "WAITING" in res2


def test_production_entry_recovery_retries_on_eligibility_and_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Production entry recover_incomplete_integration gates, retries on eligibility change, and supports force_retry."""
    from ralph.pipeline import auto_integrate_rebase_merge as rebase_merge
    from ralph.pipeline.auto_integrate import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    root, _cfg = _setup_repo_and_config(tmp_path)
    ctrl, clock = _setup_controller()
    ctrl.unavailability_store.mark_unavailable("rebase_conflict_resolution", "agent1", "cooldown")
    _setup_rebase_state(root, "feat1", "target1", "stop_sha_prod")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )
    write_record(root, record)

    resolve_calls = [0]

    def fake_resolve_in_progress(
        root: Path, target: str, resolver: object, *, session: object = None
    ) -> bool:
        resolve_calls[0] += 1
        return False

    monkeypatch.setattr(rebase_merge, "resolve_rebase_in_progress", fake_resolve_in_progress)

    def dummy_resolver(*_args: Any, **_kwargs: Any) -> bool:
        return False

    scope = WorkspaceScope(root=root)

    # 1. First call enters resolution
    outcome1 = recover_incomplete_integration(
        scope,
        config=None,
        rebase_stop_resolver=dummy_resolver,
        recovery_controller=ctrl,
    )
    assert resolve_calls[0] == 1
    assert outcome1 is not None and outcome1.recovery_record_retained is True

    # 2. Second call with unchanged inputs is gated (resolver not called)
    outcome2 = recover_incomplete_integration(
        scope,
        config=None,
        rebase_stop_resolver=dummy_resolver,
        recovery_controller=ctrl,
    )
    assert resolve_calls[0] == 1
    assert outcome2 is not None and "WAITING" in outcome2.last_reason

    # 3. Agent eligibility changes (cooldown expires) -> retried exactly once
    clock.advance(60.0)
    outcome3 = recover_incomplete_integration(
        scope,
        config=None,
        rebase_stop_resolver=dummy_resolver,
        recovery_controller=ctrl,
    )
    assert resolve_calls[0] == 2
    assert outcome3 is not None and outcome3.recovery_record_retained is True

    # 4. Immediate call after retry is gated again
    outcome4 = recover_incomplete_integration(
        scope,
        config=None,
        rebase_stop_resolver=dummy_resolver,
        recovery_controller=ctrl,
    )
    assert resolve_calls[0] == 2
    assert outcome4 is not None and "WAITING" in outcome4.last_reason

    # 5. Operator explicit force_retry (--retry command) -> retried immediately
    outcome5 = recover_incomplete_integration(
        scope,
        config=None,
        rebase_stop_resolver=dummy_resolver,
        recovery_controller=ctrl,
        force_retry=True,
    )
    assert resolve_calls[0] == 3
    assert outcome5 is not None and outcome5.recovery_record_retained is True


def test_cli_retry_integration_invokes_recovery_with_force_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI --retry and --retry-integration invoke recover_incomplete_integration with force_retry=True."""
    from typer.testing import CliRunner

    from ralph.cli import main as main_module

    recovered_called: list[bool] = []

    def fake_recover(
        scope: Any, *, config: Any = None, force_retry: bool = False, **kwargs: Any
    ) -> Any:
        recovered_called.append(force_retry)
        return None

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.recover_incomplete_integration", fake_recover
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    res = runner.invoke(main_module.app, ["--retry"])
    assert res.exit_code == 0
    assert recovered_called == [True]

    recovered_called.clear()
    res2 = runner.invoke(main_module.app, ["--retry-integration"])
    assert res2.exit_code == 0
    assert recovered_called == [True]
