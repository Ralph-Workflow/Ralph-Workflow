"""Black-box regression tests for paused rebase continuation under configuration failure.

Verifies:
1. Typed ResolutionFailureCause.CONFIG_FAILURE is preserved across paused rebase
   continuation (not falsely replaced with UNRESOLVED_CONFLICT).
2. Zero CRITICAL log lines are emitted during config failure continuation and
   subsequent retry-gated ticks (preventing CRITICAL log flood).
3. The continuation retry gate properly suppresses repeat attempts for unchanged
   broken configuration while preserving the typed CONFIG_FAILURE cause.
4. Repairing the configuration file releases the retry gate, allowing resolution
   to retry cleanly.
5. The run-loop retry path does not increment consecutive_conflicts for CONFIG_FAILURE
   and formats actionable alerts naming the configuration fix rather than generic conflict.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import auto_integrate_rebase_merge as merge_mod
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_recovery_record import (
    recover_pending_merge,
    reset_continuation_gate_state,
)
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.rebase_state import (
    RebaseOutcomeKind,
    RebaseState,
)
from ralph.pipeline.run_loop import (
    handle_pending_retry,
    integration_alert_for_state,
    run_auto_integrate_recovery_preamble,
)
from ralph.pipeline.state import PipelineState
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.timeout_seconds(5)


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


def _setup_repo_and_broken_config(tmp_path: Path) -> tuple[Path, Path]:
    cfg_dir = tmp_path / ".agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "ralph-workflow.toml"
    cfg_file.write_text("broken = [corrupted toml\n", encoding="utf-8")
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
    feat_sha: str = "feat_rebase_1",
    target_sha: str = "target_main_1",
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


def test_paused_rebase_continuation_preserves_config_failure_cause_and_prevents_critical_flood(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Continuation of a paused rebase under config failure preserves CONFIG_FAILURE and emits no CRITICAL logs."""
    root, cfg_file = _setup_repo_and_broken_config(tmp_path)
    _setup_rebase_state(root, "feat1", "target1", "stop1")

    initial_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )
    write_record(root, initial_record)

    ctrl, _clk = _setup_controller()
    fix_reason = f"fix {cfg_file} then run ralph --check-config"

    def _config_failing_resolver(root: Path, target: str, stop: Any) -> ResolutionOutcome:
        return ResolutionOutcome(
            succeeded=False,
            reason=fix_reason,
            duration_seconds=0.0,
            last_activity_kind=None,
            last_activity_at=None,
            unresolved_paths=(),
            failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
        )

    def fake_resolve(root: Path, target: str, resolver: object, session: Any = None) -> bool:
        outcome = resolver(root, target, None) if callable(resolver) else None
        if session is not None and isinstance(outcome, ResolutionOutcome):
            session.charge_conflict_budget = False
            session.terminal_reason = ResolutionTerminationReason.CONFIG_FAILURE
            session.last_failure_cause = outcome.failure_cause
            session.exhaustion_reason = str(outcome.reason)
        return False

    monkeypatch.setattr(merge_mod, "resolve_rebase_in_progress", fake_resolve)

    captured, sink_id = _capture_loguru_records()
    try:
        # Run continuation recovery directly
        outcome = recover_pending_merge(
            root,
            initial_record,
            config=None,
            rebase_stop_resolver=_config_failing_resolver,
            recovery_controller=ctrl,
        )

        # 1. Failure cause MUST be CONFIG_FAILURE, NOT UNRESOLVED_CONFLICT
        assert outcome.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
        assert outcome.recovery_record_retained is True
        assert fix_reason in (outcome.last_reason or "")

        # 2. ZERO CRITICAL log lines emitted
        critical_logs = [r for r in captured if r["level"].name == "CRITICAL"]
        assert len(critical_logs) == 0, f"Expected 0 CRITICAL logs, got: {critical_logs}"

        # 3. Disk record retains typed CONFIG_FAILURE and computed gate key
        disk_rec = read_record(root)
        assert disk_rec is not None
        assert disk_rec.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert disk_rec.resolution_gate_key is not None

        # 4. Subsequent run with unchanged state is quietly gated
        outcome2 = recover_pending_merge(
            root,
            disk_rec,
            config=None,
            rebase_stop_resolver=_config_failing_resolver,
            recovery_controller=ctrl,
        )
        assert outcome2.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert outcome2.last_reason is not None and "WAITING" in outcome2.last_reason

        # Still ZERO CRITICAL logs after the gated second run
        critical_logs_r2 = [r for r in captured if r["level"].name == "CRITICAL"]
        assert len(critical_logs_r2) == 0

        # Exactly one WAITING line emitted across both passes
        waiting_logs = [r for r in captured if "WAITING" in r["message"]]
        assert len(waiting_logs) == 1
    finally:
        with contextlib.suppress(Exception):
            logger.remove(sink_id)


def test_paused_rebase_config_repair_releases_gate_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When a failing config file is repaired, the retry gate opens and resolution proceeds."""
    root, cfg_file = _setup_repo_and_broken_config(tmp_path)
    _setup_rebase_state(root, "feat1", "target1", "stop1")

    initial_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )
    write_record(root, initial_record)

    ctrl, _clk = _setup_controller()
    resolve_calls = [0]
    fix_reason = f"fix {cfg_file} then run ralph --check-config"

    def _mock_resolver(root: Path, target: str, stop: Any) -> ResolutionOutcome:
        return ResolutionOutcome(
            succeeded=False,
            reason=fix_reason,
            duration_seconds=0.0,
            last_activity_kind=None,
            last_activity_at=None,
            unresolved_paths=(),
            failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
        )

    def fake_resolve(root: Path, target: str, resolver: object, session: Any = None) -> bool:
        resolve_calls[0] += 1
        outcome = resolver(root, target, None) if callable(resolver) else None
        if session is not None and isinstance(outcome, ResolutionOutcome):
            session.charge_conflict_budget = False
            session.terminal_reason = ResolutionTerminationReason.CONFIG_FAILURE
            session.last_failure_cause = outcome.failure_cause
            session.exhaustion_reason = str(outcome.reason)
        return False

    monkeypatch.setattr(merge_mod, "resolve_rebase_in_progress", fake_resolve)

    # Initial attempt establishes gated state
    outcome1 = recover_pending_merge(
        root,
        initial_record,
        config=None,
        rebase_stop_resolver=_mock_resolver,
        recovery_controller=ctrl,
    )
    assert resolve_calls[0] == 1
    assert outcome1.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE

    # Second pass with unchanged broken config is gated
    rec_after_1 = read_record(root)
    assert rec_after_1 is not None
    outcome2 = recover_pending_merge(
        root,
        rec_after_1,
        config=None,
        rebase_stop_resolver=_mock_resolver,
        recovery_controller=ctrl,
    )
    assert resolve_calls[0] == 1  # Not called again
    assert outcome2.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert "WAITING" in (outcome2.last_reason or "")

    cfg_file.write_text("[general]\nmodel = 'repaired-model'\n", encoding="utf-8")

    # Third pass detects fingerprint change across workspace config sources
    outcome3 = recover_pending_merge(
        root,
        rec_after_1,
        config=None,
        rebase_stop_resolver=_mock_resolver,
        recovery_controller=ctrl,
    )
    assert outcome3 is not None
    assert resolve_calls[0] == 2  # Invoked again!


def test_paused_rebase_run_loop_preserves_cause_and_does_not_bump_conflicts(tmp_path: Path) -> None:
    """Run-loop handling of paused rebase with CONFIG_FAILURE does not bump conflict counter and formats alert."""
    fix_reason = f"fix {tmp_path}/.agent/ralph-workflow.toml then run ralph --check-config"
    rebase = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason=fix_reason,
        last_failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
        consecutive_conflicts=2,
    )
    state = PipelineState(phase="planning", rebase=rebase)
    mock_ctx = MagicMock()
    mock_ctx.latest_state = []
    mock_ctx.sleep = MagicMock()
    mock_ctx.config_failure_watcher = None

    handled = handle_pending_retry(
        state,
        mock_ctx,
        rebase,
        detail="paused rebase config error",
        backend=MagicMock(),
    )

    # 1. consecutive_conflicts must NOT be charged
    assert handled.rebase.consecutive_conflicts == 2

    # 2. Alert formatting cleanly surfaces CONFIG_FAILURE and fix instructions
    alert = integration_alert_for_state(handled)
    assert alert is not None
    assert "CONFIG_FAILURE" in alert
    assert "fix " in alert
    assert "then run ralph --check-config" in alert
    assert "UNRESOLVED_CONFLICT" not in alert


def test_paused_rebase_recovery_preamble_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End-to-end recovery preamble with paused rebase and broken config preserves cause and emits no CRITICAL logs."""
    root, cfg_file = _setup_repo_and_broken_config(tmp_path)
    _setup_rebase_state(root, "feat1", "target1", "stop1")

    initial_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )
    write_record(root, initial_record)

    fix_reason = f"fix {cfg_file} then run ralph --check-config"

    def _mock_resolver(root: Path, target: str, stop: Any) -> ResolutionOutcome:
        return ResolutionOutcome(
            succeeded=False,
            reason=fix_reason,
            duration_seconds=0.0,
            last_activity_kind=None,
            last_activity_at=None,
            unresolved_paths=(),
            failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
        )

    def fake_resolve(root: Path, target: str, resolver: object, session: Any = None) -> bool:
        outcome = resolver(root, target, None) if callable(resolver) else None
        if session is not None and isinstance(outcome, ResolutionOutcome):
            session.charge_conflict_budget = False
            session.terminal_reason = ResolutionTerminationReason.CONFIG_FAILURE
            session.last_failure_cause = outcome.failure_cause
            session.exhaustion_reason = str(outcome.reason)
        return False

    monkeypatch.setattr(merge_mod, "resolve_rebase_in_progress", fake_resolve)

    scope = WorkspaceScope(root=root)

    captured, sink_id = _capture_loguru_records()
    try:
        outcome = run_auto_integrate_recovery_preamble(
            scope,
            config=None,
            rebase_stop_resolver=_mock_resolver,
        )
        assert outcome is not None
        assert outcome.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
        assert len([r for r in captured if r["level"].name == "CRITICAL"]) == 0
    finally:
        with contextlib.suppress(Exception):
            logger.remove(sink_id)
