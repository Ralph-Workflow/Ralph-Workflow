from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    parse_record_payload,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_recovery_record import (
    recover_pending_merge,
    reset_continuation_gate_state,
)
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
    run_git(["init", "-q", "-b", "main"], cwd=tmp_path, label="setup-init")
    run_git(["config", "user.name", "test"], cwd=tmp_path, label="setup-config-user")
    run_git(["config", "user.email", "test@test.com"], cwd=tmp_path, label="setup-config-email")
    run_git(["commit", "--allow-empty", "-m", "init"], cwd=tmp_path, label="setup-commit")
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


def test_retained_rebase_resolution_without_resolver_gates_durable_record(
    tmp_path: Path,
) -> None:
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

    captured, sink_id = _capture_loguru_records()
    try:
        outcome1 = recover_pending_merge(
            root,
            read_record(root) or record,
            config=None,
            rebase_stop_resolver=None,
            recovery_controller=ctrl,
        )
        assert outcome1.recovery_record_retained is True
        assert outcome1.last_reason == "rebase resolution retained; continuation resolver required"

        critical_logs_r1 = [r for r in captured if r["level"].name == "CRITICAL"]
        waiting_logs_r1 = [r for r in captured if "WAITING" in r["message"]]
        assert len(critical_logs_r1) == 1
        assert len(waiting_logs_r1) == 0

        record_after_r1 = read_record(root)
        assert record_after_r1 is not None
        assert record_after_r1.resolution_gate_key is not None

        outcome2 = recover_pending_merge(
            root,
            record_after_r1,
            config=None,
            rebase_stop_resolver=None,
            recovery_controller=ctrl,
        )
        assert outcome2.recovery_record_retained is True
        assert "WAITING" in outcome2.last_reason

        waiting_logs_r2 = [r for r in captured if "WAITING" in r["message"]]
        assert len(waiting_logs_r2) == 1

        critical_logs_r2 = [r for r in captured if r["level"].name == "CRITICAL"]
        assert len(critical_logs_r2) == 1

        outcome3 = recover_pending_merge(
            root,
            read_record(root) or record,
            config=None,
            rebase_stop_resolver=None,
            recovery_controller=ctrl,
        )
        assert outcome3.recovery_record_retained is True
        assert "WAITING" in outcome3.last_reason

        assert len([r for r in captured if r["level"].name == "CRITICAL"]) == 1
        assert len([r for r in captured if "WAITING" in r["message"]]) == 1

        reloaded_record = read_record(root)
        assert reloaded_record is not None
        assert reloaded_record.resolution_gate_key is not None
        assert reloaded_record.resolution_gate_key == record_after_r1.resolution_gate_key

        outcome_reload = recover_pending_merge(
            root,
            reloaded_record,
            config=None,
            rebase_stop_resolver=None,
            recovery_controller=ctrl,
        )
        assert outcome_reload.recovery_record_retained is True
        assert "WAITING" in outcome_reload.last_reason
        assert len([r for r in captured if r["level"].name == "CRITICAL"]) == 1
        assert len([r for r in captured if "WAITING" in r["message"]]) == 1
    finally:
        logger.remove(sink_id)


def test_second_process_reload_without_resolver_gates_immediately(
    tmp_path: Path,
) -> None:
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

    recover_pending_merge(
        root,
        record,
        config=None,
        rebase_stop_resolver=None,
        recovery_controller=ctrl1,
    )
    persisted_record = read_record(root)
    assert persisted_record is not None
    assert persisted_record.resolution_gate_key is not None

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
            rebase_stop_resolver=None,
            recovery_controller=ctrl2,
        )
        assert outcome_p2.recovery_record_retained is True
        assert "WAITING" in outcome_p2.last_reason

        waiting_logs = [r for r in captured if "WAITING" in r["message"]]
        critical_logs = [r for r in captured if r["level"].name == "CRITICAL"]
        assert len(waiting_logs) == 1
        assert len(critical_logs) == 0
    finally:
        logger.remove(sink_id)


def test_missing_resolution_gate_key_is_ungated(
    tmp_path: Path,
) -> None:
    root, _cfg = _setup_repo_and_config(tmp_path)
    ctrl, _clk = _setup_controller()
    _setup_rebase_state(root, "feat1", "target1", "stop_sha_1")

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

    loaded = parse_record_payload(legacy_payload)
    assert loaded is not None
    assert loaded.resolution_gate_key is None

    outcome = recover_pending_merge(
        root,
        loaded,
        config=None,
        rebase_stop_resolver=None,
        recovery_controller=ctrl,
    )
    assert outcome.recovery_record_retained is True
    assert outcome.last_reason == "rebase resolution retained; continuation resolver required"

    saved = read_record(root)
    assert saved is not None
    assert saved.resolution_gate_key is not None
