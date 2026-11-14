from __future__ import annotations

import contextlib
import functools
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_recovery_record import (
    recover_pending_merge,
    reset_continuation_gate_state,
)
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.pipeline.resolution_outcome import ResolvedOutcome
from ralph.pipeline.run_loop import (
    handle_pending_retry,
    integration_alert_for_state,
    run_auto_integrate_recovery_preamble,
)
from ralph.pipeline.state import PipelineState
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.timeout_seconds(10)


@pytest.fixture(autouse=True)
def _clean_gate_state() -> None:
    reset_continuation_gate_state()


@pytest.fixture(autouse=True)
def _isolate_home_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    fake_home = tmp_path_factory.mktemp("fake_home")
    fake_xdg = fake_home / ".config"
    fake_xdg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg))


def _capture_loguru_records() -> tuple[list[dict[str, Any]], int]:
    captured: list[dict[str, Any]] = []

    def _sink(message: object) -> None:
        rec = getattr(message, "record", None)
        if rec is not None:
            captured.append(rec)

    sink_id = logger.add(_sink, level="DEBUG")
    return captured, sink_id


@functools.lru_cache(maxsize=1)
def _get_paused_rebase_template() -> tuple[Path, str, str]:
    base = Path(tempfile.mkdtemp(prefix="paused-rebase-template-"))
    run_git(["init", "-b", "main"], cwd=base, label="test:setup:init")
    run_git(["config", "user.name", "Test User"], cwd=base, label="test:setup:user")
    run_git(["config", "user.email", "test@example.com"], cwd=base, label="test:setup:email")
    run_git(["config", "gc.auto", "0"], cwd=base, label="test:setup:gc")
    run_git(["config", "core.fsync", "none"], cwd=base, label="test:setup:fsync")
    run_git(["config", "commit.gpgSign", "false"], cwd=base, label="test:setup:gpg")
    (base / "conflict.txt").write_text("base content\n", encoding="utf-8")
    run_git(["add", "conflict.txt"], cwd=base, label="test:setup:add")
    run_git(["commit", "-m", "base commit"], cwd=base, label="test:setup:commit")

    run_git(["checkout", "-b", "feature"], cwd=base, label="test:setup:checkout-feature")
    (base / "conflict.txt").write_text("feature content\n", encoding="utf-8")
    run_git(["commit", "-am", "feature commit"], cwd=base, label="test:setup:commit-feature")

    run_git(["checkout", "main"], cwd=base, label="test:setup:checkout-main")
    (base / "conflict.txt").write_text("main content\n", encoding="utf-8")
    run_git(["commit", "-am", "main commit"], cwd=base, label="test:setup:commit-main")

    run_git(["checkout", "feature"], cwd=base, label="test:setup:checkout-feature-2")
    run_git(["rebase", "main"], cwd=base, label="test:setup:rebase")

    feat_sha = run_git(
        ["rev-parse", "feature"], cwd=base, label="test:setup:rev-feature"
    ).stdout.strip()
    main_sha = run_git(["rev-parse", "main"], cwd=base, label="test:setup:rev-main").stdout.strip()
    return base, feat_sha, main_sha


def _setup_repo_with_paused_rebase_and_broken_config(
    tmp_path: Path,
) -> tuple[Path, Path, str, str]:
    base_template, feat_sha, main_sha = _get_paused_rebase_template()
    root = tmp_path / "repo"
    shutil.copytree(base_template, root)

    cfg_dir = root / ".agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "ralph-workflow.toml"
    cfg_file.write_text("broken = [invalid toml syntax\n", encoding="utf-8")

    return root, cfg_file, feat_sha, main_sha


def _setup_controller(clock: FakeClock | None = None) -> tuple[RecoveryController, FakeClock]:
    clk = clock or FakeClock()
    opts = RecoveryControllerOptions(clock=clk)
    ctrl = RecoveryController(options=opts)
    ctrl.register_phase_chain("rebase_conflict_resolution", ["agent1"])
    return ctrl, clk


def _setup_agent_rebase_resolver(
    root: Path,
    agent_invocations: list[int] | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Any:
    mock_display = MagicMock()
    mock_policy = MagicMock()
    mock_registry = MagicMock()
    mock_registry.installed_agents.return_value = ["agent1"]
    mock_policy.pipeline.rebase_conflict_resolution = ["agent1"]
    mock_config = MagicMock()
    mock_deps = MagicMock()
    mock_scope = WorkspaceScope(root=root)

    if monkeypatch is not None and agent_invocations is not None:

        def fake_agent_pipeline(*args: Any, **kwargs: Any) -> ResolvedOutcome:
            agent_invocations[0] += 1
            (root / "conflict.txt").write_text("resolved content\n", encoding="utf-8")
            return ResolvedOutcome()

        import ralph.pipeline.auto_integrate_agent as aia_mod

        monkeypatch.setattr(aia_mod, "run_rebase_conflict_resolution_outcome", fake_agent_pipeline)

    return build_agent_rebase_stop_resolver(
        policy_bundle=mock_policy,
        registry=mock_registry,
        display=mock_display,
        config=mock_config,
        pipeline_deps=mock_deps,
        workspace_scope=mock_scope,
    )


def test_paused_rebase_continuation_preserves_config_failure_cause_and_prevents_critical_flood(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, cfg_file, feat_sha, main_sha = _setup_repo_with_paused_rebase_and_broken_config(tmp_path)

    initial_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat_sha,
        pre_target_sha=main_sha,
        resolving_rebase=True,
    )
    write_record(root, initial_record)

    ctrl, _clk = _setup_controller()
    agent_invocations = [0]
    resolver = _setup_agent_rebase_resolver(root, agent_invocations, monkeypatch)

    captured, sink_id = _capture_loguru_records()
    try:
        outcome = recover_pending_merge(
            root,
            initial_record,
            config=None,
            rebase_stop_resolver=resolver,
            recovery_controller=ctrl,
        )

        assert outcome.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
        assert outcome.recovery_record_retained is True
        assert str(cfg_file) in (outcome.last_reason or "")
        assert "ralph --check-config" in (outcome.last_reason or "")
        assert outcome.resolution_exhausted is False
        assert outcome.resolution_exhaustion_reason is None
        assert outcome.consecutive_conflicts == 0

        assert agent_invocations[0] == 0

        warn_logs = [r for r in captured if r["level"].name == "WARNING"]
        config_warns = [
            r
            for r in warn_logs
            if str(cfg_file) in r["message"]
            and "ralph --check-config" in r["message"]
            and "strict configuration behaviour in effect" in r["message"]
        ]
        assert len(config_warns) >= 1, f"Expected config failure warning log, got: {warn_logs}"

        critical_logs = [r for r in captured if r["level"].name == "CRITICAL"]
        assert len(critical_logs) == 0, f"Expected 0 CRITICAL logs, got: {critical_logs}"

        disk_rec = read_record(root)
        assert disk_rec is not None
        assert disk_rec.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert disk_rec.resolution_gate_key is not None

        outcome2 = recover_pending_merge(
            root,
            disk_rec,
            config=None,
            rebase_stop_resolver=resolver,
            recovery_controller=ctrl,
        )
        assert outcome2.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert outcome2.last_reason is not None and "WAITING" in outcome2.last_reason
        assert agent_invocations[0] == 0

        critical_logs_r2 = [r for r in captured if r["level"].name == "CRITICAL"]
        assert len(critical_logs_r2) == 0

        waiting_logs = [r for r in captured if "WAITING" in r["message"]]
        assert len(waiting_logs) == 1
    finally:
        with contextlib.suppress(Exception):
            logger.remove(sink_id)


def test_paused_rebase_config_repair_releases_gate_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, cfg_file, feat_sha, main_sha = _setup_repo_with_paused_rebase_and_broken_config(tmp_path)

    initial_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat_sha,
        pre_target_sha=main_sha,
        resolving_rebase=True,
    )
    write_record(root, initial_record)

    ctrl, _clk = _setup_controller()
    agent_invocations = [0]
    resolver = _setup_agent_rebase_resolver(root, agent_invocations, monkeypatch)

    outcome1 = recover_pending_merge(
        root,
        initial_record,
        config=None,
        rebase_stop_resolver=resolver,
        recovery_controller=ctrl,
    )
    assert agent_invocations[0] == 0
    assert outcome1.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert str(cfg_file) in (outcome1.last_reason or "")
    assert "ralph --check-config" in (outcome1.last_reason or "")

    rec_after_1 = read_record(root)
    assert rec_after_1 is not None
    outcome2 = recover_pending_merge(
        root,
        rec_after_1,
        config=None,
        rebase_stop_resolver=resolver,
        recovery_controller=ctrl,
    )
    assert agent_invocations[0] == 0
    assert outcome2.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert "WAITING" in (outcome2.last_reason or "")

    cfg_file.write_text("[general]\n", encoding="utf-8")

    outcome3 = recover_pending_merge(
        root,
        rec_after_1,
        config=None,
        rebase_stop_resolver=resolver,
        recovery_controller=ctrl,
    )
    assert outcome3 is not None
    assert agent_invocations[0] == 1
    assert not (root / ".git" / "rebase-merge").exists()
    assert outcome3.last_outcome == RebaseOutcomeKind.RECOVERED
    assert outcome3.last_failure_cause != ResolutionFailureCause.CONFIG_FAILURE
    assert read_record(root) is None

    outcome4 = recover_pending_merge(
        root,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha=feat_sha,
            pre_target_sha=main_sha,
        ),
        config=None,
        rebase_stop_resolver=resolver,
        recovery_controller=ctrl,
    )
    assert outcome4 is not None
    assert agent_invocations[0] == 1


def test_paused_rebase_run_loop_preserves_cause_and_does_not_bump_conflicts(tmp_path: Path) -> None:
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

    assert handled.rebase.consecutive_conflicts == 2

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
    root, cfg_file, feat_sha, main_sha = _setup_repo_with_paused_rebase_and_broken_config(tmp_path)

    initial_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat_sha,
        pre_target_sha=main_sha,
        resolving_rebase=True,
    )
    write_record(root, initial_record)

    agent_invocations = [0]
    resolver = _setup_agent_rebase_resolver(root, agent_invocations, monkeypatch)
    scope = WorkspaceScope(root=root)

    captured, sink_id = _capture_loguru_records()
    try:
        outcome = run_auto_integrate_recovery_preamble(
            scope,
            config=None,
            rebase_stop_resolver=resolver,
        )
        assert outcome is not None
        assert outcome.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
        assert str(cfg_file) in (outcome.last_reason or "")
        assert "ralph --check-config" in (outcome.last_reason or "")
        assert outcome.resolution_exhausted is False
        assert outcome.consecutive_conflicts == 0
        assert agent_invocations[0] == 0
        assert len([r for r in captured if r["level"].name == "CRITICAL"]) == 0

        cfg_file.write_text("[general]\n", encoding="utf-8")
        outcome_repaired = run_auto_integrate_recovery_preamble(
            scope,
            config=None,
            rebase_stop_resolver=resolver,
        )
        assert outcome_repaired is not None
        assert outcome_repaired.last_outcome == RebaseOutcomeKind.RECOVERED
        assert agent_invocations[0] == 1
        assert not (root / ".git" / "rebase-merge").exists()
    finally:
        with contextlib.suppress(Exception):
            logger.remove(sink_id)
