from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from loguru import logger
from rich.console import Console

from ralph.display.auto_integrate_message import OperationDisplayState
from ralph.display.context import make_display_context
from ralph.display.status_bar import StatusBar, render_status_bar
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.git.subprocess_runner import run_git
from ralph.pipeline import (
    RebaseOutcomeKind,
    RebaseResolutionResult,
    resolve_conflicted_rebase,
    resolve_rebase_with_config,
)
from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_recovery_record import recover_pending_merge
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
from ralph.pipeline.run_loop import (
    LoopContext,
    build_status_bar_model,
    integration_alert_for_state,
    log_waiting_state,
    resolve_paused_rebase,
)
from ralph.pipeline.state import PipelineState
from ralph.workspace.context import WorkspaceContextError
from ralph.workspace.scope import WorkspaceScope

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_integrated_transport_reaches_run_loop_alert_and_status_bar": (
        "use public StatusBar.model property instead of private _model attribute"
    ),
    "test_integrated_transport_config_failure_reaches_run_loop_alert_and_status_bar": (
        "avoid soft line-wrapping breaking long path in console status bar assertion"
    ),
}


def test_context_failure_enum_and_not_spent() -> None:
    assert ResolutionTerminationReason.CONTEXT_FAILURE == "CONTEXT_FAILURE"
    assert ResolutionTerminationReason.CONTEXT_FAILURE in RESOLVER_NOT_SPENT_TERMINATION_REASONS
    assert ResolutionFailureCause.CONTEXT_FAILURE == "CONTEXT_FAILURE"


def test_resolver_generic_context_failure_populates_all_sinks(tmp_path: Path) -> None:
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

    assert isinstance(outcome, ResolutionOutcome)
    assert outcome.succeeded is False
    assert outcome.failure_cause == ResolutionFailureCause.CONTEXT_FAILURE
    assert "target worktree context failed: simulated filesystem error" in str(outcome.reason)

    assert session.charge_conflict_budget is False
    assert session.terminal_reason == ResolutionTerminationReason.CONTEXT_FAILURE
    assert session.last_failure_cause == ResolutionFailureCause.CONTEXT_FAILURE
    assert session.exhaustion_reason == "target worktree context failed: simulated filesystem error"


def test_resolver_config_related_exception_classifies_as_config_failure(
    tmp_path: Path,
) -> None:
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
    assert short_circuit.consecutive_conflicts == 0


def _setup_paused_rebase_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(["init", "-b", "main"], cwd=repo, label="test:setup:init")
    run_git(["config", "user.name", "Test User"], cwd=repo, label="test:setup:user")
    run_git(["config", "user.email", "test@example.com"], cwd=repo, label="test:setup:email")
    run_git(["config", "gc.auto", "0"], cwd=repo, label="test:setup:gc")
    run_git(["config", "core.fsync", "none"], cwd=repo, label="test:setup:fsync")
    run_git(["config", "commit.gpgSign", "false"], cwd=repo, label="test:setup:gpg")
    (repo / "conflict.txt").write_text("base content\n", encoding="utf-8")
    run_git(["add", "conflict.txt"], cwd=repo, label="test:setup:add")
    run_git(["commit", "-m", "base commit"], cwd=repo, label="test:setup:commit")

    run_git(["checkout", "-b", "feature"], cwd=repo, label="test:setup:checkout-feature")
    (repo / "conflict.txt").write_text("feature content\n", encoding="utf-8")
    run_git(["commit", "-am", "feature commit"], cwd=repo, label="test:setup:commit-feature")

    run_git(["checkout", "main"], cwd=repo, label="test:setup:checkout-main")
    (repo / "conflict.txt").write_text("main content\n", encoding="utf-8")
    run_git(["commit", "-am", "main commit"], cwd=repo, label="test:setup:commit-main")

    run_git(["checkout", "feature"], cwd=repo, label="test:setup:checkout-feature-2")
    run_git(["rebase", "main"], cwd=repo, label="test:setup:rebase")
    return repo


def _make_loop_context(repo: Path) -> MagicMock:
    mock_ctx = MagicMock(spec=LoopContext)
    mock_ctx.workspace_scope = WorkspaceScope(root=repo)
    mock_ctx.policy_bundle = MagicMock()
    mock_ctx.policy_bundle.pipeline = MagicMock()
    mock_ctx.registry = MagicMock()
    mock_ctx.active_display = MagicMock()
    mock_ctx.config = MagicMock()
    mock_ctx.config.conflict_resolution = None
    mock_ctx.pipeline_deps = MagicMock()
    mock_ctx.display_context = None
    mock_ctx.controller = MagicMock()
    mock_ctx.controller.waiting_state_payload.return_value = [("agent1", 5000, 1)]
    mock_ctx.last_waiting_state_sig = None
    mock_ctx.last_waiting_state_phase = None
    return mock_ctx


def test_integrated_transport_reaches_run_loop_alert_and_status_bar(
    tmp_path: Path,
) -> None:
    repo = _setup_paused_rebase_repo(tmp_path)
    mock_ctx = _make_loop_context(repo)

    feat_sha = run_git(
        ["rev-parse", "feature"], cwd=repo, label="test:setup:rev-feature"
    ).stdout.strip()
    main_sha = run_git(["rev-parse", "main"], cwd=repo, label="test:setup:rev-main").stdout.strip()
    initial_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat_sha,
        pre_target_sha=main_sha,
        resolving_rebase=True,
    )
    write_record(repo, initial_record)

    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="INFO")

    try:
        resolver = build_agent_rebase_stop_resolver(
            policy_bundle=mock_ctx.policy_bundle,
            registry=mock_ctx.registry,
            display=mock_ctx.active_display,
            config=mock_ctx.config,
            pipeline_deps=mock_ctx.pipeline_deps,
            workspace_scope=mock_ctx.workspace_scope,
            display_context=mock_ctx.display_context,
        )

        with patch(
            "ralph.pipeline.auto_integrate_agent.workspace_context",
            side_effect=RuntimeError("boom"),
        ):
            resolved = resolve_paused_rebase(mock_ctx, "main")
            outcome = recover_pending_merge(
                repo,
                initial_record,
                config=None,
                rebase_stop_resolver=resolver,
                recovery_controller=mock_ctx.controller,
            )

        assert resolved is False

        paused_rebase_warns = [
            r["message"]
            for r in records
            if "the paused rebase for 'main' was not resolved" in r["message"]
        ]
        assert len(paused_rebase_warns) == 1
        assert "target worktree context failed: boom" in paused_rebase_warns[0]
        assert "cause: CONTEXT_FAILURE" in paused_rebase_warns[0]
        assert "no terminal evidence" not in paused_rebase_warns[0]

        chain_warns = [
            r["message"]
            for r in records
            if "without the resolver chain being spent" in r["message"]
        ]
        assert len(chain_warns) >= 1
        assert "ended on CONTEXT_FAILURE" in chain_warns[0]
        assert "no terminal evidence" not in chain_warns[0]

        assert outcome.last_failure_cause == ResolutionFailureCause.CONTEXT_FAILURE
        assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
        assert "target worktree context failed: boom" in (outcome.last_reason or "")
        assert outcome.resolution_exhausted is False
        assert outcome.resolution_exhaustion_reason is None

        disk_rec = read_record(repo)
        assert disk_rec is not None
        assert disk_rec.last_failure_cause == ResolutionFailureCause.CONTEXT_FAILURE
        assert disk_rec.resolution_gate_key is not None

        rebase_state = outcome.set_operation_display_state(OperationDisplayState.WAITING)
        state = PipelineState(phase="planning", rebase=rebase_state)

        alert = integration_alert_for_state(state)
        assert alert is not None
        assert "CONTEXT_FAILURE: target worktree context failed: boom" in alert
        assert "resolution required" in alert
        assert "no terminal evidence" not in alert

        model = build_status_bar_model(state, mock_ctx.policy_bundle, repo, attention="waiting")
        console = Console(file=io.StringIO(), width=100, force_terminal=True, color_system=None)
        ctx_disp = make_display_context(console=console, env={})
        rendered = render_status_bar(model, ctx_disp, home=str(tmp_path))
        plain_status = rendered.plain
        assert "target worktree context failed: boom" in " ".join(plain_status.split())
        assert "no terminal evidence" not in plain_status

        display_mock = MagicMock()
        display_mock.terminal_width = 100
        sb = StatusBar(display_mock)
        sb.update(model)
        assert sb.model == model

        log_waiting_state(state, mock_ctx, "planning", rebase_state.last_reason, 5000)
        waiting_records = [r["message"] for r in records if "WAITING state" in r["message"]]
        assert len(waiting_records) == 1
        assert "target worktree context failed: boom" in waiting_records[0]
        assert "no terminal evidence" not in waiting_records[0]
    finally:
        logger.remove(sink_id)


def test_integrated_transport_config_failure_reaches_run_loop_alert_and_status_bar(
    tmp_path: Path,
) -> None:
    repo = _setup_paused_rebase_repo(tmp_path)
    mock_ctx = _make_loop_context(repo)

    feat_sha = run_git(
        ["rev-parse", "feature"], cwd=repo, label="test:setup:rev-feature-cfg"
    ).stdout.strip()
    main_sha = run_git(
        ["rev-parse", "main"], cwd=repo, label="test:setup:rev-main-cfg"
    ).stdout.strip()
    initial_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat_sha,
        pre_target_sha=main_sha,
        resolving_rebase=True,
    )
    write_record(repo, initial_record)

    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda msg: records.append(dict(msg.record)), level="INFO")

    try:
        bad_cfg = repo / ".agent" / "ralph-workflow.toml"
        fix_msg = f"fix {bad_cfg} then run ralph --check-config"

        resolver = build_agent_rebase_stop_resolver(
            policy_bundle=mock_ctx.policy_bundle,
            registry=mock_ctx.registry,
            display=mock_ctx.active_display,
            config=mock_ctx.config,
            pipeline_deps=mock_ctx.pipeline_deps,
            workspace_scope=mock_ctx.workspace_scope,
            display_context=mock_ctx.display_context,
        )

        with patch(
            "ralph.pipeline.auto_integrate_agent.workspace_context",
            side_effect=WorkspaceContextError(
                file=bad_cfg,
                reason="syntax error",
                fix=fix_msg,
            ),
        ):
            resolved = resolve_paused_rebase(mock_ctx, "main")
            outcome = recover_pending_merge(
                repo,
                initial_record,
                config=None,
                rebase_stop_resolver=resolver,
                recovery_controller=mock_ctx.controller,
            )

        assert resolved is False

        cfg_rebase_warns = [
            r["message"]
            for r in records
            if "the paused rebase for 'main' was not resolved" in r["message"]
        ]
        assert len(cfg_rebase_warns) == 1
        assert fix_msg in cfg_rebase_warns[0]
        assert "cause: CONFIG_FAILURE" in cfg_rebase_warns[0]
        assert "no terminal evidence" not in cfg_rebase_warns[0]

        assert outcome.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
        assert fix_msg in (outcome.last_reason or "")
        assert outcome.resolution_exhausted is False
        assert outcome.resolution_exhaustion_reason is None

        disk_rec = read_record(repo)
        assert disk_rec is not None
        assert disk_rec.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert disk_rec.resolution_gate_key is not None

        rebase_state_cfg = outcome.set_operation_display_state(OperationDisplayState.WAITING)
        state_cfg = PipelineState(phase="planning", rebase=rebase_state_cfg)

        alert_cfg = integration_alert_for_state(state_cfg)
        assert alert_cfg is not None
        assert "CONFIG_FAILURE" in alert_cfg
        assert fix_msg in alert_cfg
        assert "no terminal evidence" not in alert_cfg

        model_cfg = build_status_bar_model(
            state_cfg, mock_ctx.policy_bundle, repo, attention="waiting"
        )
        console = Console(file=io.StringIO(), width=300, force_terminal=True, color_system=None)
        ctx_disp = make_display_context(console=console, env={})
        rendered_cfg = render_status_bar(model_cfg, ctx_disp, home=str(tmp_path))
        assert fix_msg in rendered_cfg.plain.replace("\n", "")
        assert "no terminal evidence" not in rendered_cfg.plain

        log_waiting_state(state_cfg, mock_ctx, "planning", rebase_state_cfg.last_reason, 5000)
        waiting_records_cfg = [r["message"] for r in records if "WAITING state" in r["message"]]
        assert len(waiting_records_cfg) == 1
        assert fix_msg in waiting_records_cfg[0]
        assert "no terminal evidence" not in waiting_records_cfg[0]
    finally:
        logger.remove(sink_id)
