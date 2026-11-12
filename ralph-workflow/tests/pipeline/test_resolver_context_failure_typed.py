"""Tests for Unit U1: Typed cause for non-WorkspaceContextError resolver declines.

Asserts:
(1) a resolver whose target-context entry raises a generic exception yields
    failure_cause=CONTEXT_FAILURE and a reason naming the failure through
    resolve_rebase_with_config;
(2) integration test through resolve_rebase_with_config plus warning assertions:
    terminal reason is CONTEXT_FAILURE and warning text carries the preserved
    reason (never "no terminal evidence");
(3) the chain is NOT recorded exhausted;
(4) consistency across session terminal reason, outcome failure_cause, and
    rebase session state;
(5) integrated transport asserting preserved reason reaches run-loop warning
    and status bar alert.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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
    bind_active_rebase_resolution_session,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.run_loop import integration_alert_for_state
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope


def test_context_failure_enum_and_not_spent() -> None:
    """CONTEXT_FAILURE must exist on ResolutionTerminationReason and be in RESOLVER_NOT_SPENT."""
    assert ResolutionTerminationReason.CONTEXT_FAILURE == "CONTEXT_FAILURE"
    assert ResolutionTerminationReason.CONTEXT_FAILURE in RESOLVER_NOT_SPENT_TERMINATION_REASONS
    assert ResolutionFailureCause.CONTEXT_FAILURE == "CONTEXT_FAILURE"


def test_resolver_generic_context_failure_populates_all_sinks(tmp_path: Path) -> None:
    """A generic exception during context entry yields CONTEXT_FAILURE across all sinks."""
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
        sha="deadbeef",
        subject="test rebase stop",
        conflicted_files=("file.txt",),
        stop_index=1,
        stop_cap=10,
    )

    with (
        patch(
            "ralph.pipeline.auto_integrate_agent._resolve_within_target_context",
            side_effect=RuntimeError("simulated filesystem error"),
        ),
        bind_active_rebase_resolution_session(session),
    ):
        outcome = resolver(tmp_path, "main", stop)

    # Outcome checks
    assert isinstance(outcome, ResolutionOutcome)
    assert outcome.succeeded is False
    assert outcome.failure_cause == ResolutionFailureCause.CONTEXT_FAILURE
    assert "target worktree context failed: simulated filesystem error" in str(outcome.reason)

    # Session sink checks
    assert session.charge_conflict_budget is False
    assert session.terminal_reason == ResolutionTerminationReason.CONTEXT_FAILURE
    assert session.last_failure_cause == ResolutionFailureCause.CONTEXT_FAILURE
    assert session.exhaustion_reason == "target worktree context failed: simulated filesystem error"


def test_resolver_config_related_exception_classifies_as_config_failure(
    tmp_path: Path,
) -> None:
    """An exception mentioning a config file classifies as CONFIG_FAILURE across all sinks."""
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
        sha="deadbeef",
        subject="test rebase stop",
        conflicted_files=("file.txt",),
        stop_index=1,
        stop_cap=10,
    )

    with (
        patch(
            "ralph.pipeline.auto_integrate_agent._resolve_within_target_context",
            side_effect=RuntimeError("syntax error in agents.toml"),
        ),
        bind_active_rebase_resolution_session(session),
    ):
        outcome = resolver(tmp_path, "main", stop)

    assert isinstance(outcome, ResolutionOutcome)
    assert outcome.succeeded is False
    assert outcome.failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert session.charge_conflict_budget is False
    assert session.terminal_reason == ResolutionTerminationReason.CONFIG_FAILURE
    assert session.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE


def test_resolve_rebase_with_config_carries_context_failure_never_no_terminal_evidence(
    tmp_path: Path,
) -> None:
    """resolve_rebase_with_config preserves CONTEXT_FAILURE and reason, never 'no terminal evidence'."""
    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="WARNING")

    def _failing_resolver(root: Path, target: str, stop: RebaseStop) -> ResolutionOutcome:
        session = ResolutionSession()
        session.charge_conflict_budget = False
        session.terminal_reason = ResolutionTerminationReason.CONTEXT_FAILURE
        session.last_failure_cause = ResolutionFailureCause.CONTEXT_FAILURE
        session.exhaustion_reason = "target worktree context failed: simulated context fault"
        return ResolutionOutcome(
            succeeded=False,
            reason="target worktree context failed: simulated context fault",
            duration_seconds=0.0,
            last_activity_kind=None,
            last_activity_at=None,
            unresolved_paths=(),
            failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
        )

    def _fake_resolve_rebase(root: Any, target: Any, resolver: Any, session: Any = None) -> bool:
        outcome = resolver(root, target, None)
        if session is not None and isinstance(outcome, ResolutionOutcome):
            session.charge_conflict_budget = False
            session.last_failure_cause = outcome.failure_cause
            session.terminal_reason = ResolutionTerminationReason.CONTEXT_FAILURE
            session.exhaustion_reason = outcome.reason
        return False

    mock_runner = MagicMock()
    mock_runner.resolve_rebase.side_effect = _fake_resolve_rebase

    try:
        res = resolve_rebase_with_config(
            tmp_path,
            "main",
            _failing_resolver,
            None,
            agent_runner=mock_runner,
        )
        assert isinstance(res, RebaseResolutionResult)
        assert res.succeeded is False
        assert res.failure_cause == ResolutionFailureCause.CONTEXT_FAILURE
        assert res.reason == "target worktree context failed: simulated context fault"

        # Assert warning logs never contain 'no terminal evidence' and do contain CONTEXT_FAILURE
        warning_messages = [
            r["message"]
            for r in records
            if "without the resolver chain being spent" in r["message"]
        ]
        assert len(warning_messages) >= 1
        assert all("no terminal evidence" not in msg for msg in warning_messages)
        assert any("ended on CONTEXT_FAILURE" in msg for msg in warning_messages)
    finally:
        logger.remove(sink_id)


def test_resolve_conflicted_rebase_carries_context_failure_not_exhausted(
    tmp_path: Path,
) -> None:
    """resolve_conflicted_rebase persists CONTEXT_FAILURE and does not mark chain exhausted."""
    mock_display = MagicMock()
    mock_deps = MagicMock()
    mock_scope = WorkspaceScope(root=tmp_path)
    mock_policy = MagicMock()
    mock_registry = MagicMock()
    mock_config = MagicMock()

    resolver = build_agent_rebase_stop_resolver(
        policy_bundle=mock_policy,
        registry=mock_registry,
        display=mock_display,
        config=mock_config,
        pipeline_deps=mock_deps,
        workspace_scope=mock_scope,
    )

    def _fake_resolve_rebase(root: Any, target: Any, res_fn: Any, session: Any = None) -> bool:
        stop = RebaseStop(
            sha="1234abc",
            subject="conflict stop",
            conflicted_files=("a.txt",),
            stop_index=1,
            stop_cap=10,
        )
        outcome = res_fn(root, target, stop)
        if session is not None and isinstance(outcome, ResolutionOutcome):
            session.charge_conflict_budget = False
            session.last_failure_cause = outcome.failure_cause
            session.terminal_reason = ResolutionTerminationReason.CONTEXT_FAILURE
            session.exhaustion_reason = outcome.reason
        return False

    mock_runner = MagicMock()
    mock_runner.resolve_rebase.side_effect = _fake_resolve_rebase

    with (
        patch(
            "ralph.pipeline.auto_integrate_agent._resolve_within_target_context",
            side_effect=PermissionError("access denied to target worktree"),
        ),
        patch(
            "ralph.pipeline.auto_integrate_rebase_merge.set_resolving_rebase",
            return_value=True,
        ),
        patch(
            "ralph.pipeline.auto_integrate_rebase_merge.read_record",
            return_value=None,
        ),
    ):
        result = resolve_conflicted_rebase(
            tmp_path,
            "main",
            resolver,
            conflict_resolution_config=None,
            agent_runner=mock_runner,
            display=mock_display,
        )

    assert result is not None
    assert result.short_circuit is not None
    short_circuit = result.short_circuit
    assert short_circuit.last_outcome == RebaseOutcomeKind.CONFLICT
    assert short_circuit.last_failure_cause == ResolutionFailureCause.CONTEXT_FAILURE
    assert "target worktree context failed: access denied to target worktree" in str(
        short_circuit.last_reason
    )
    # The chain must not be marked exhausted:
    assert short_circuit.consecutive_conflicts == 0


def test_integrated_transport_reaches_run_loop_alert_and_status_bar(
    tmp_path: Path,
) -> None:
    """Preserved CONTEXT_FAILURE and reason reach run-loop alert and status bar."""
    reason_str = "target worktree context failed: disk I/O error"
    rebase = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason=reason_str,
        last_failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
    )
    state = PipelineState(phase="planning", rebase=rebase)

    # 1. Integration alert
    alert = integration_alert_for_state(state)
    assert alert is not None
    assert "CONTEXT_FAILURE: target worktree context failed: disk I/O error" in alert
    assert "resolution required" in alert
