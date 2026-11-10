"""Tests for Unit 7a: CONFIG_FAILURE contract, resolver path, and run_loop integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from loguru import logger

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import (
    RebaseOutcomeKind,
    RebaseResolutionResult,
    resolve_conflicted_rebase,
    resolve_rebase_with_config,
)
from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver
from ralph.pipeline.conflict_resolution import (
    RebaseStop,
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.rebase_loop import (
    bind_active_rebase_resolution_session,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.run_loop import (
    LoopContext,
    handle_pending_retry,
    integration_alert_for_state,
    log_waiting_state,
    resolve_paused_rebase,
)
from ralph.pipeline.state import PipelineState
from ralph.workspace.context import WorkspaceContextError, workspace_context
from ralph.workspace.scope import WorkspaceScope


def test_resolution_failure_cause_and_termination_reason() -> None:
    """CONFIG_FAILURE must exist on both enums and be marked as not spent."""
    assert ResolutionFailureCause.CONFIG_FAILURE == "CONFIG_FAILURE"
    assert ResolutionTerminationReason.CONFIG_FAILURE == "CONFIG_FAILURE"
    assert ResolutionTerminationReason.CONFIG_FAILURE in RESOLVER_NOT_SPENT_TERMINATION_REASONS


def test_workspace_context_error_structure(tmp_path: Path) -> None:
    """WorkspaceContextError must carry file, reason, and fix."""
    bad_file = tmp_path / ".agent" / "ralph-workflow.toml"
    err = WorkspaceContextError(
        file=bad_file,
        reason="syntax error on line 3",
        fix=f"fix {bad_file} then run ralph --check-config",
    )
    assert err.file == bad_file
    assert err.reason == "syntax error on line 3"
    assert err.fix == f"fix {bad_file} then run ralph --check-config"
    assert "then run ralph --check-config" in str(err)


def test_workspace_context_wraps_config_toml_error(tmp_path: Path) -> None:
    """Invalid TOML syntax in .agent/ralph-workflow.toml raises WorkspaceContextError."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_file = agent_dir / "ralph-workflow.toml"
    cfg_file.write_text("invalid = [to m l", encoding="utf-8")

    try:
        with workspace_context(tmp_path):
            pass
    except WorkspaceContextError as exc:
        assert exc.file == cfg_file
        assert "fix" in exc.fix.lower()
        assert "ralph --check-config" in exc.fix
    else:
        raise AssertionError("Expected WorkspaceContextError was not raised")


def test_workspace_context_wraps_system_exit(tmp_path: Path) -> None:
    """Schema validation failure triggering SystemExit raises WorkspaceContextError."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_file = agent_dir / "ralph-workflow.toml"
    # General max_cycles expects an integer or None; passing an invalid string triggers ValidationError
    cfg_file.write_text('[general]\nmax_cycles = "not-an-int"\n', encoding="utf-8")

    try:
        with workspace_context(tmp_path):
            pass
    except WorkspaceContextError as exc:
        assert exc.file == cfg_file
        assert "ralph --check-config" in exc.fix
    else:
        raise AssertionError("Expected WorkspaceContextError was not raised")


def test_auto_integrate_agent_resolver_catches_workspace_context_error(tmp_path: Path) -> None:
    """Resolver catches WorkspaceContextError, sets session non-charging, returns CONFIG_FAILURE."""
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_file = agent_dir / "ralph-workflow.toml"
    cfg_file.write_text("corrupted = [to ml", encoding="utf-8")

    session = ResolutionSession()
    mock_display = MagicMock()
    mock_policy = MagicMock()
    mock_registry = MagicMock()
    mock_config = MagicMock()
    mock_deps = MagicMock()
    mock_scope = WorkspaceScope(root=tmp_path)

    resolver = build_agent_rebase_stop_resolver(
        policy_bundle=mock_policy,
        registry=mock_registry,
        display=mock_display,
        config=mock_config,
        pipeline_deps=mock_deps,
        workspace_scope=mock_scope,
    )

    stop = RebaseStop(
        sha="abc1234",
        subject="test conflict",
        conflicted_files=("file.txt",),
        stop_index=1,
        stop_cap=10,
    )

    with bind_active_rebase_resolution_session(session):
        outcome = resolver(tmp_path, "main", stop)

    assert isinstance(outcome, ResolutionOutcome)
    assert outcome.succeeded is False
    assert outcome.failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert f"fix {cfg_file} then run ralph --check-config" in str(outcome.reason)
    assert session.charge_conflict_budget is False
    assert session.terminal_reason == ResolutionTerminationReason.CONFIG_FAILURE
    assert session.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE


def test_rebase_resolution_result_unpacking() -> None:
    """RebaseResolutionResult supports 2-tuple unpacking, 3-tuple unpacking, and property access."""
    res = RebaseResolutionResult(False, "reason text", ResolutionFailureCause.CONFIG_FAILURE)
    assert res.succeeded is False
    assert res.reason == "reason text"
    assert res.failure_cause == ResolutionFailureCause.CONFIG_FAILURE

    # 2-tuple unpacking (for backward-compatible callers like _integration_continuation.py)
    unpacked_two = res[:2]
    r2, re2 = unpacked_two
    assert r2 is False
    assert re2 == "reason text"

    # 3-tuple unpacking
    r3, re3, c3 = res
    assert r3 is False
    assert re3 == "reason text"
    assert c3 == ResolutionFailureCause.CONFIG_FAILURE


def test_resolve_conflicted_rebase_carries_config_failure(tmp_path: Path) -> None:
    """resolve_conflicted_rebase carries CONFIG_FAILURE into record_conflict."""

    def _fake_resolve_rebase(root: Any, target: Any, resolver: Any, session: Any = None) -> bool:
        outcome = resolver(root, target, None)
        if session is not None and isinstance(outcome, ResolutionOutcome):
            session.charge_conflict_budget = False
            session.last_failure_cause = outcome.failure_cause
            session.terminal_reason = ResolutionTerminationReason.CONFIG_FAILURE
            session.exhaustion_reason = (
                "fix .agent/ralph-workflow.toml then run ralph --check-config"
            )
        return False

    mock_runner = MagicMock()
    mock_runner.resolve_rebase.side_effect = _fake_resolve_rebase

    def _failing_resolver(root: Path, target: str, stop: RebaseStop) -> ResolutionOutcome:
        session = ResolutionSession()
        session.charge_conflict_budget = False
        session.terminal_reason = ResolutionTerminationReason.CONFIG_FAILURE
        session.last_failure_cause = ResolutionFailureCause.CONFIG_FAILURE
        session.exhaustion_reason = "fix .agent/ralph-workflow.toml then run ralph --check-config"
        return ResolutionOutcome(
            succeeded=False,
            reason=ResolutionTerminationReason.CONFIG_FAILURE,
            duration_seconds=0.0,
            last_activity_kind=None,
            last_activity_at=None,
            unresolved_paths=(),
            failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
        )

    res = resolve_rebase_with_config(
        tmp_path,
        "main",
        _failing_resolver,
        None,
        agent_runner=mock_runner,
    )
    assert res.succeeded is False

    # Simulate run through resolve_conflicted_rebase with monkeypatched set_resolving_rebase
    from ralph.pipeline import auto_integrate_rebase_merge as merge_mod

    set_resolving = MagicMock(return_value=True)
    read_rec = MagicMock(return_value=None)
    orig_set = merge_mod.set_resolving_rebase
    orig_read = merge_mod.read_record
    try:
        merge_mod.set_resolving_rebase = set_resolving
        merge_mod.read_record = read_rec
        result = resolve_conflicted_rebase(
            tmp_path,
            "main",
            _failing_resolver,
            None,
            None,
            agent_runner=mock_runner,
        )
        assert result is not None
        assert result.short_circuit is not None
        assert result.short_circuit.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    finally:
        merge_mod.set_resolving_rebase = orig_set
        merge_mod.read_record = orig_read


def test_run_loop_does_not_bump_consecutive_conflicts_on_config_failure() -> None:
    """handle_pending_retry does NOT charge conflict-attempt counter for CONFIG_FAILURE."""
    rebase = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason="fix .agent/ralph-workflow.toml then run ralph --check-config",
        last_failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
        consecutive_conflicts=3,
    )
    state = PipelineState(phase="planning", rebase=rebase)
    mock_ctx = MagicMock()
    mock_ctx.latest_state = []
    mock_ctx.sleep = MagicMock()

    new_state = handle_pending_retry(
        state,
        mock_ctx,
        rebase,
        detail="conflict detail",
        backend=MagicMock(),
    )
    assert new_state.rebase.consecutive_conflicts == 3


def test_run_loop_integration_alert_format_with_cause_and_fix() -> None:
    """WAITING message at run_loop.py:743 formats cause + fix."""
    fix_msg = "fix /path/to/.agent/ralph-workflow.toml then run ralph --check-config"
    rebase = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason=fix_msg,
        last_failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
    )
    state = PipelineState(phase="planning", rebase=rebase)
    alert = integration_alert_for_state(state)
    assert alert is not None
    assert "CONFIG_FAILURE" in alert
    assert fix_msg in alert
    assert "integration conflict" in alert


def test_run_loop_waiting_log_gate_emits_once() -> None:
    """WAITING log gate emits only once per state change, not on duplicate ticks."""
    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="INFO")
    try:
        mock_ctx = MagicMock(spec=LoopContext)
        mock_ctx.last_waiting_state_phase = None
        mock_ctx.last_waiting_state_sig = None
        mock_ctx.controller = MagicMock()
        mock_ctx.controller.waiting_state_payload.return_value = []
        mock_ctx.active_display = MagicMock()
        mock_ctx.sleep = MagicMock()

        state = PipelineState(phase="planning", last_unavailability_reason="agents busy")
        # Direct call to log_waiting_state
        log_waiting_state(state, mock_ctx, "planning", "agents busy", 5000)
        assert len(records) == 1

        # Second call with identical state - gated!
        log_waiting_state(state, mock_ctx, "planning", "agents busy", 5000)
        assert len(records) == 1

        # Third call with new state - emits!
        log_waiting_state(state, mock_ctx, "planning", "cooldown active", 5000)
        assert len(records) == 2
    finally:
        logger.remove(sink_id)


def test_run_loop_logs_carried_reason() -> None:
    """resolve_paused_rebase logs the carried reason on failure."""
    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="WARNING")
    try:
        mock_ctx = MagicMock(spec=LoopContext)
        mock_ctx.workspace_scope = MagicMock()
        mock_ctx.workspace_scope.root = Path("/tmp")
        mock_ctx.policy_bundle = MagicMock()
        mock_ctx.registry = MagicMock()
        mock_ctx.active_display = MagicMock()
        mock_ctx.config = MagicMock()
        mock_ctx.pipeline_deps = MagicMock()
        mock_ctx.display_context = None

        mock_executor = MagicMock()
        mock_executor.continue_rebase.return_value = (
            False,
            "fix .agent/ralph-workflow.toml then run ralph --check-config",
            ResolutionFailureCause.CONFIG_FAILURE,
        )

        resolved = resolve_paused_rebase(
            mock_ctx,
            "main",
            executor=mock_executor,
        )
        assert resolved is False
        matching = [
            r["message"]
            for r in records
            if "fix .agent/ralph-workflow.toml then run ralph --check-config" in r["message"]
        ]
        assert len(matching) == 1
    finally:
        logger.remove(sink_id)
