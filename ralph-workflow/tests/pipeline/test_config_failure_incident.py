"""Tests for Unit 7b: Incident path and selection handling of WorkspaceContextError."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from loguru import logger

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import IntegrationAction
from ralph.pipeline._integration_diagnostic import (
    diagnose_retained_integration,
    handoff_integration_diagnostic,
)
from ralph.pipeline._integration_diagnostic_handoff import (
    _next_resolver,
    check_target_worktree_context,
)
from ralph.pipeline._pending_merge_repair import (
    handoff_pending_merge_repair,
    repair_pending_merge,
)
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.operation_evidence import (
    InMemoryOperationEvidenceCollector,
    OperationFact,
)
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._resolver_controller import make_resolver_controller


def test_check_target_worktree_context_identifies_bad_config(tmp_path: Path) -> None:
    """check_target_worktree_context returns reason text on corrupt config, None on valid."""
    worktree = tmp_path / "wt"
    agent_dir = worktree / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_file = agent_dir / "ralph-workflow.toml"
    cfg_file.write_text("invalid [ toml syntax", encoding="utf-8")

    reason = check_target_worktree_context(worktree)
    assert reason is not None
    assert f"fix {cfg_file} then run ralph --check-config" in reason

    # Fix file to valid TOML
    cfg_file.write_text('[general]\nproject_name = "test"\n', encoding="utf-8")
    assert check_target_worktree_context(worktree) is None


def test_next_resolver_context_precondition_prevents_selection_announcement(
    tmp_path: Path,
) -> None:
    """_next_resolver returns CONFIG_FAILURE reason and skips selection when target context is broken."""
    worktree = tmp_path / "wt"
    agent_dir = worktree / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_file = agent_dir / "ralph-workflow.toml"
    cfg_file.write_text("syntax error = [", encoding="utf-8")

    controller = make_resolver_controller()
    captured_logs: list[str] = []
    handler_id = logger.add(captured_logs.append, level="DEBUG")

    try:
        result = _next_resolver(controller, ["agent_a", "agent_b"], target_root=worktree)
    finally:
        logger.remove(handler_id)

    # Result must be the CONFIG_FAILURE reason string, NOT an agent name
    assert result is not None
    assert result not in ["agent_a", "agent_b"]
    assert f"fix {cfg_file} then run ralph --check-config" in result

    # No selection announcement must be logged
    for log_msg in captured_logs:
        assert "Selected agent" not in log_msg
        assert "Phase resolution:" not in log_msg


def test_next_resolver_proceeds_with_selection_when_context_valid(tmp_path: Path) -> None:
    """_next_resolver calls choose_resolver and announces selection when target context is valid."""
    worktree = tmp_path / "wt"
    agent_dir = worktree / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_file = agent_dir / "ralph-workflow.toml"
    cfg_file.write_text('[general]\nproject_name = "test"\n', encoding="utf-8")

    controller = make_resolver_controller()
    captured_logs: list[str] = []
    handler_id = logger.add(captured_logs.append, level="DEBUG")

    try:
        result = _next_resolver(controller, ["agent_a", "agent_b"], target_root=worktree)
    finally:
        logger.remove(handler_id)

    assert result == "agent_a"
    assert any("Selected agent agent_a" in msg for msg in captured_logs)


def test_diagnostic_path_config_failure_sets_config_failure_no_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diagnostic handoff on config failure sets CONFIG_FAILURE without bumping attempts or logging selection."""
    root = tmp_path / "root"
    root.mkdir(parents=True)
    (root / ".agent").mkdir(parents=True)

    wt = tmp_path / "target_wt"
    (wt / ".agent").mkdir(parents=True)
    bad_cfg = wt / ".agent" / "ralph-workflow.toml"
    bad_cfg.write_text("bad = [to ml", encoding="utf-8")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        operation_kind="target_reconcile",
        merge_commit_repair_attempts=2,
        owning_worktree=str(wt),
    )
    write_record(root, record)

    monkeypatch.setattr(
        "ralph.pipeline._integration_diagnostic.decide_seam_action",
        lambda _root, *, record=None: IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
    )
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )

    controller = make_resolver_controller()
    captured_logs: list[str] = []
    handler_id = logger.add(captured_logs.append, level="DEBUG")

    try:
        released = handoff_integration_diagnostic(
            root=root,
            record=record,
            failure="test failure",
            agents=("agent_a",),
            invoke=lambda a, p: True,
            controller=controller,
            collector=collector,
        )
    finally:
        logger.remove(handler_id)

    assert released is False

    updated = read_record(root)
    assert updated is not None
    assert updated.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert updated.merge_commit_repair_attempts == 2  # NOT bumped!
    assert f"fix {bad_cfg} then run ralph --check-config" in (updated.repair_last_error or "")

    for msg in captured_logs:
        assert "Selected agent" not in msg


def test_diagnose_retained_integration_handles_workspace_context_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """diagnose_retained_integration catches WorkspaceContextError without bumping attempts."""
    root = tmp_path / "root"
    root.mkdir(parents=True)
    (root / ".agent").mkdir(parents=True)

    bad_cfg = root / ".agent" / "ralph-workflow.toml"
    bad_cfg.write_text("bad = [to ml", encoding="utf-8")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        merge_commit_repair_attempts=1,
    )
    write_record(root, record)

    monkeypatch.setattr(
        "ralph.pipeline._integration_diagnostic.decide_seam_action",
        lambda _root, *, record=None: IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
    )
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )

    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session.resolution_chain_agents",
        lambda _bundle: ("agent_a",),
    )
    ctx = MagicMock()
    ctx.workspace_scope = WorkspaceScope(root)
    ctx.controller = make_resolver_controller()
    ctx.pipeline_deps = make_test_pipeline_deps(MagicMock())
    ctx.active_display = None
    ctx.display_context = None
    ctx.config = MagicMock()
    ctx.config.conflict_resolution.total_resolution_cap_seconds = 100.0
    ctx.policy_bundle = MagicMock()
    ctx.registry = MagicMock()
    ctx.registry.get.return_value = MagicMock()

    result = diagnose_retained_integration(ctx, "failure detail", collector=collector)
    assert result is False

    updated = read_record(root)
    assert updated is not None
    assert updated.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert updated.merge_commit_repair_attempts == 1  # NOT bumped!
    assert f"fix {bad_cfg} then run ralph --check-config" in (updated.repair_last_error or "")


def test_pending_merge_repair_catches_workspace_context_error_without_bumping(
    tmp_path: Path,
) -> None:
    """Pending merge repair catches WorkspaceContextError without bumping attempts or logging selection."""
    root = tmp_path / "root"
    root.mkdir(parents=True)
    (root / ".agent").mkdir(parents=True)

    wt = tmp_path / "owner_wt"
    (wt / ".agent").mkdir(parents=True)
    bad_cfg = wt / ".agent" / "ralph-workflow.toml"
    bad_cfg.write_text("broken = [ toml", encoding="utf-8")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        merge_commit_repair_attempts=3,
        operation_kind="target_reconcile",
        owning_worktree=str(wt),
    )
    write_record(root, record)

    controller = make_resolver_controller()
    captured_logs: list[str] = []
    handler_id = logger.add(captured_logs.append, level="DEBUG")

    try:
        success = repair_pending_merge(
            workspace_scope=WorkspaceScope(root),
            config=MagicMock(),
            pipeline_deps=make_test_pipeline_deps(MagicMock()),
            policy_bundle=MagicMock(),
            display=None,
            display_context=None,
            agents=("agent_a",),
            failure="commit hook failed",
            controller=controller,
        )
    finally:
        logger.remove(handler_id)

    assert success is False

    updated = read_record(root)
    assert updated is not None
    assert updated.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert updated.merge_commit_repair_attempts == 3  # NOT bumped!
    assert f"fix {bad_cfg} then run ralph --check-config" in (updated.repair_last_error or "")

    for msg in captured_logs:
        assert "Selected agent" not in msg


def test_handoff_pending_merge_repair_avoids_selection_on_config_failure(
    tmp_path: Path,
) -> None:
    """handoff_pending_merge_repair returns False and skips selection when target config is corrupt."""
    root = tmp_path / "root"
    root.mkdir(parents=True)
    (root / ".agent").mkdir(parents=True)

    bad_cfg = root / ".agent" / "ralph-workflow.toml"
    bad_cfg.write_text("bad = [syntax", encoding="utf-8")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        merge_commit_repair_attempts=0,
    )
    write_record(root, record)

    controller = make_resolver_controller()
    captured_logs: list[str] = []
    handler_id = logger.add(captured_logs.append, level="DEBUG")

    try:
        success = handoff_pending_merge_repair(
            root=root,
            record=record,
            failure="merge failed",
            agents=("agent_a",),
            invoke=lambda a, p: True,
            controller=controller,
        )
    finally:
        logger.remove(handler_id)

    assert success is False

    updated = read_record(root)
    assert updated is not None
    assert updated.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert updated.merge_commit_repair_attempts == 0  # NOT bumped!

    for msg in captured_logs:
        assert "Selected agent" not in msg
