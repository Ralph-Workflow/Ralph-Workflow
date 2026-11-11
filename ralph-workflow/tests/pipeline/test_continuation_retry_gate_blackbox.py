"""Black-box integration tests for durable continuation retry gate.

Covers:
1. --retry invokes resolver once for retained rebase; unchanged identity invokes none;
2. config repair or newly eligible agent invokes once for retained rebase;
3. --retry invokes resolver once for retained merge; unchanged identity invokes none;
4. config repair or newly eligible agent invokes once for retained merge.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest
from loguru import logger
from typer.testing import CliRunner

from ralph.agents.timeout_clock import FakeClock
from ralph.cli import main as main_module
from ralph.config.loader import load_config
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import auto_integrate_rebase_merge as merge_mod
from ralph.pipeline import auto_integrate_resolve as air_mod
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_recovery_record import (
    compute_continuation_retry_gate_key,
    reset_continuation_gate_state,
)
from ralph.pipeline.auto_integrate_resolve import MERGE_COMMIT_PENDING, MergeResult
from ralph.pipeline.run_loop import run_auto_integrate_recovery_preamble
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from ralph.workspace.scope import WorkspaceScope


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


def _setup_merge_state(
    root: Path,
    feat_sha: str = "0" * 40,
    target_sha: str = "a" * 40,
) -> None:
    git_dir = root / ".git"
    (git_dir / "HEAD").write_text(f"{feat_sha}\n", encoding="utf-8")
    (git_dir / "MERGE_HEAD").write_text(f"{target_sha}\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text(f"{target_sha}\n", encoding="utf-8")


def _setup_controller(clock: FakeClock | None = None) -> tuple[RecoveryController, FakeClock]:
    clk = clock or FakeClock()
    opts = RecoveryControllerOptions(clock=clk)
    ctrl = RecoveryController(options=opts)
    ctrl.register_phase_chain("rebase_conflict_resolution", ["agent1", "agent2"])
    return ctrl, clk


def test_blackbox_retained_rebase_retry_invokes_resolver_and_gates_repeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Black-box retained rebase fixture: --retry invokes resolver once; unchanged identity

    invokes none and yields no repeated CRITICAL log.
    """
    root, _cfg_file = _setup_repo_and_config(tmp_path)
    _setup_rebase_state(root, "feat1", "target1", "stop1")

    rec = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )
    write_record(root, rec)

    resolve_calls = [0]

    def fake_resolve(root: Path, target: str, resolver: object, session: object = None) -> bool:
        resolve_calls[0] += 1
        return False

    monkeypatch.setattr(merge_mod, "resolve_rebase_in_progress", fake_resolve)
    monkeypatch.chdir(root)

    runner = CliRunner()

    # 1. ralph --retry invokes exactly one resolver
    res = runner.invoke(main_module.app, ["--retry"])
    assert res.exit_code != 0
    assert resolve_calls[0] == 1

    rec_after_retry = read_record(root)
    assert rec_after_retry is not None and rec_after_retry.resolution_gate_key is not None

    captured, sink_id = _capture_loguru_records()
    try:
        # 2. Unchanged non-forced identity invokes none and yields zero repeated CRITICAL
        scope = WorkspaceScope(root)
        cfg = load_config(workspace_scope=scope)

        outcome2 = run_auto_integrate_recovery_preamble(scope, config=cfg)
        assert resolve_calls[0] == 1  # unchanged non-forced identity invokes none
        assert outcome2 is not None and "WAITING" in outcome2.last_reason

        critical_count_2 = len([r for r in captured if r["level"].name == "CRITICAL"])
        assert critical_count_2 == 0  # NO repeated CRITICAL during gated unchanged identity!
    finally:
        with contextlib.suppress(Exception):
            logger.remove(sink_id)


def test_blackbox_retained_rebase_config_repair_and_eligibility(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Black-box retained rebase fixture: config repair or newly eligible agent invokes once."""
    root, cfg_file = _setup_repo_and_config(tmp_path)
    _setup_rebase_state(root, "feat1", "target1", "stop1")

    rec = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )
    write_record(root, rec)

    resolve_calls = [0]

    def fake_resolve(root: Path, target: str, resolver: object, session: object = None) -> bool:
        resolve_calls[0] += 1
        return False

    monkeypatch.setattr(merge_mod, "resolve_rebase_in_progress", fake_resolve)
    monkeypatch.chdir(root)

    scope = WorkspaceScope(root)
    cfg = load_config(workspace_scope=scope)

    # Initial attempt to establish gated state
    outcome1 = run_auto_integrate_recovery_preamble(scope, config=cfg)
    assert resolve_calls[0] == 1
    assert outcome1 is not None and outcome1.recovery_record_retained is True

    captured, sink_id = _capture_loguru_records()
    try:
        # Config repair invokes exactly once
        cfg_file.write_text("[general]\nmodel = 'repaired'\n", encoding="utf-8")
        cfg_repaired = load_config(workspace_scope=scope)
        outcome2 = run_auto_integrate_recovery_preamble(scope, config=cfg_repaired)
        assert resolve_calls[0] == 2  # config repair invokes exactly once
        assert outcome2 is not None and outcome2.recovery_record_retained is True

        # Gated again immediately after the repair attempt
        critical_count_before_repeat = len([r for r in captured if r["level"].name == "CRITICAL"])
        outcome2_repeat = run_auto_integrate_recovery_preamble(scope, config=cfg_repaired)
        assert resolve_calls[0] == 2
        assert outcome2_repeat is not None and "WAITING" in outcome2_repeat.last_reason
        critical_count_after_repeat = len([r for r in captured if r["level"].name == "CRITICAL"])
        assert critical_count_after_repeat == critical_count_before_repeat

        # Newly eligible agent invokes exactly once
        ctrl, clock = _setup_controller()
        ctrl.unavailability_store.mark_unavailable(
            "rebase_conflict_resolution", "agent1", "cooldown"
        )
        gate_rec = read_record(root)
        assert gate_rec is not None
        gate_key = compute_continuation_retry_gate_key(
            root, gate_rec, cfg_repaired, recovery_controller=ctrl
        )
        write_record(root, gate_rec.with_resolution_gate_key(gate_key))

        run_auto_integrate_recovery_preamble(scope, config=cfg_repaired, recovery_controller=ctrl)
        assert resolve_calls[0] == 2  # still gated

        # Cooldown expires -> agent1 becomes eligible -> exactly one attempt
        clock.advance(60.0)
        outcome3_after = run_auto_integrate_recovery_preamble(
            scope, config=cfg_repaired, recovery_controller=ctrl
        )
        assert resolve_calls[0] == 3  # newly eligible agent invoked resolver once
        assert outcome3_after is not None and outcome3_after.recovery_record_retained is True
    finally:
        with contextlib.suppress(Exception):
            logger.remove(sink_id)


def test_blackbox_retained_merge_retry_invokes_resolver_and_gates_repeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Black-box retained merge fixture: --retry invokes resolver once; unchanged identity

    invokes none and yields no repeated CRITICAL log.
    """
    root, _cfg_file = _setup_repo_and_config(tmp_path)
    feat_sha = "0" * 40
    target_sha = "a" * 40
    _setup_merge_state(root, feat_sha, target_sha)

    rec = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat_sha,
        pre_target_sha=target_sha,
        resolving_merge=True,
        conflicted_files=("f.py",),
    )
    write_record(root, rec)

    resolve_calls = [0]

    def fake_merge_resolve(root: Path, target: str, resolver: object) -> MergeResult:
        resolve_calls[0] += 1
        return MergeResult(
            MERGE_COMMIT_PENDING,
            "unresolved merge",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )

    monkeypatch.setattr(air_mod, "resolve_and_commit_with_reason", fake_merge_resolve)
    monkeypatch.chdir(root)

    runner = CliRunner()

    # 1. ralph --retry invokes exactly one resolver
    res = runner.invoke(main_module.app, ["--retry"])
    assert res.exit_code != 0
    assert resolve_calls[0] == 1

    rec_after_retry = read_record(root)
    assert rec_after_retry is not None and rec_after_retry.resolution_gate_key is not None

    captured, sink_id = _capture_loguru_records()
    try:
        # 2. Unchanged non-forced identity invokes none and yields zero repeated CRITICAL
        scope = WorkspaceScope(root)
        cfg = load_config(workspace_scope=scope)

        outcome2 = run_auto_integrate_recovery_preamble(scope, config=cfg)
        assert resolve_calls[0] == 1  # unchanged non-forced identity invokes none
        assert outcome2 is not None and "WAITING" in outcome2.last_reason

        critical_count_2 = len([r for r in captured if r["level"].name == "CRITICAL"])
        assert critical_count_2 == 0  # NO repeated CRITICAL during gated unchanged identity!
    finally:
        with contextlib.suppress(Exception):
            logger.remove(sink_id)


def test_blackbox_retained_merge_config_repair_and_eligibility(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Black-box retained merge fixture: config repair or newly eligible agent invokes once."""
    root, cfg_file = _setup_repo_and_config(tmp_path)
    feat_sha = "0" * 40
    target_sha = "a" * 40
    _setup_merge_state(root, feat_sha, target_sha)

    rec = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat_sha,
        pre_target_sha=target_sha,
        resolving_merge=True,
        conflicted_files=("f.py",),
    )
    write_record(root, rec)

    resolve_calls = [0]

    def fake_merge_resolve(root: Path, target: str, resolver: object) -> MergeResult:
        resolve_calls[0] += 1
        return MergeResult(
            MERGE_COMMIT_PENDING,
            "unresolved merge",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )

    monkeypatch.setattr(air_mod, "resolve_and_commit_with_reason", fake_merge_resolve)
    monkeypatch.chdir(root)

    scope = WorkspaceScope(root)
    cfg = load_config(workspace_scope=scope)

    # Initial attempt to establish gated state
    outcome1 = run_auto_integrate_recovery_preamble(scope, config=cfg)
    assert resolve_calls[0] == 1
    assert outcome1 is not None and outcome1.recovery_record_retained is True

    captured, sink_id = _capture_loguru_records()
    try:
        # Config repair invokes exactly once
        cfg_file.write_text("[general]\nmodel = 'repaired'\n", encoding="utf-8")
        cfg_repaired = load_config(workspace_scope=scope)
        outcome2 = run_auto_integrate_recovery_preamble(scope, config=cfg_repaired)
        assert resolve_calls[0] == 2  # config repair invokes exactly once
        assert outcome2 is not None and outcome2.recovery_record_retained is True

        # Gated again immediately after repair attempt
        crit_before = len([r for r in captured if r["level"].name == "CRITICAL"])
        outcome2_repeat = run_auto_integrate_recovery_preamble(scope, config=cfg_repaired)
        assert resolve_calls[0] == 2
        assert outcome2_repeat is not None and "WAITING" in outcome2_repeat.last_reason
        assert len([r for r in captured if r["level"].name == "CRITICAL"]) == crit_before

        # Newly eligible agent invokes exactly once
        ctrl, clock = _setup_controller()
        ctrl.unavailability_store.mark_unavailable(
            "rebase_conflict_resolution", "agent1", "cooldown"
        )
        gate_rec = read_record(root)
        assert gate_rec is not None
        gate_key = compute_continuation_retry_gate_key(
            root, gate_rec, cfg_repaired, recovery_controller=ctrl
        )
        write_record(root, gate_rec.with_resolution_gate_key(gate_key))

        run_auto_integrate_recovery_preamble(scope, config=cfg_repaired, recovery_controller=ctrl)
        assert resolve_calls[0] == 2  # still gated

        # Cooldown expires -> agent1 becomes eligible -> exactly one attempt
        clock.advance(60.0)
        outcome3_after = run_auto_integrate_recovery_preamble(
            scope, config=cfg_repaired, recovery_controller=ctrl
        )
        assert resolve_calls[0] == 3  # newly eligible agent invoked resolver once
        assert outcome3_after is not None and outcome3_after.recovery_record_retained is True
    finally:
        with contextlib.suppress(Exception):
            logger.remove(sink_id)
