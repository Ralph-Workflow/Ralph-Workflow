from __future__ import annotations

from pathlib import Path

import pytest

import ralph.pipeline.attempt_runner as attempt_runner_mod
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.auto_integrate_recovery_record import continue_retained_resolution
from ralph.pipeline.effect_executor import (
    install_agent_effect_dependencies,
    reset_agent_effect_dependencies,
)
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions


def _setup_repo_with_rebase(tmp_path: Path) -> Path:
    cfg_dir = tmp_path / ".agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ralph-workflow.toml").write_text("[general]\nmodel = 'test'\n", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
    (git_dir / "objects").mkdir(parents=True, exist_ok=True)
    (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (git_dir / "refs" / "heads" / "main").write_text("0" * 40 + "\n", encoding="utf-8")
    rebase_dir = git_dir / "rebase-merge"
    rebase_dir.mkdir(parents=True, exist_ok=True)
    (rebase_dir / "head-name").write_text("refs/heads/feature\n", encoding="utf-8")
    (rebase_dir / "orig-head").write_text("feat1\n", encoding="utf-8")
    (rebase_dir / "onto").write_text("target1\n", encoding="utf-8")
    (rebase_dir / "stopped-sha").write_text("stop_sha_1\n", encoding="utf-8")
    return tmp_path


def test_continue_retained_resolution_without_shared_controller_does_not_invent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _setup_repo_with_rebase(tmp_path)
    build_calls: list[object] = []

    def fail_build(*_args: object, **_kwargs: object) -> RecoveryController:
        build_calls.append((_args, _kwargs))
        pytest.fail("must not invent a RecoveryController for retained continuation")

    monkeypatch.setattr(attempt_runner_mod, "build_entry_point_recovery_controller", fail_build)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat1",
        pre_target_sha="target1",
        resolving_rebase=True,
    )

    launch_calls = [0]

    def counting_resolver(*_args: object, **_kwargs: object) -> RebaseResolutionResult:
        launch_calls[0] += 1
        return RebaseResolutionResult(
            succeeded=False,
            reason="should not launch",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )

    result = continue_retained_resolution(
        root,
        record,
        None,
        None,
        counting_resolver,
    )

    assert build_calls == []
    assert launch_calls[0] == 0
    assert isinstance(result, str)
    assert "WAITING: continuation retry key unavailable" in result
    assert "recovery controller required" in result


def test_continue_retained_resolution_explicit_controller_bypasses_builder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build_calls: list[object] = []

    def fail_build(*_args: object, **_kwargs: object) -> RecoveryController:
        build_calls.append((_args, _kwargs))
        pytest.fail(
            "build_entry_point_recovery_controller should not be called when explicit controller is passed"
        )

    monkeypatch.setattr(attempt_runner_mod, "build_entry_point_recovery_controller", fail_build)

    explicit_controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=5))

    record = IntegrationRecord(
        phase="integrating",
        target="",
        pre_feature_sha="feature-sha",
        pre_target_sha=None,
    )

    result = continue_retained_resolution(
        tmp_path,
        record,
        None,
        None,
        None,
        recovery_controller=explicit_controller,
    )

    assert isinstance(result, str)
    assert build_calls == []


def test_continue_retained_resolution_ambient_controller_bypasses_builder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build_calls: list[object] = []

    def fail_build(*_args: object, **_kwargs: object) -> RecoveryController:
        build_calls.append((_args, _kwargs))
        pytest.fail(
            "build_entry_point_recovery_controller should not be called when ambient controller exists"
        )

    monkeypatch.setattr(attempt_runner_mod, "build_entry_point_recovery_controller", fail_build)

    ambient_controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=5))
    token = install_agent_effect_dependencies(recovery_controller=ambient_controller)
    try:
        record = IntegrationRecord(
            phase="integrating",
            target="",
            pre_feature_sha="feature-sha",
            pre_target_sha=None,
        )

        result = continue_retained_resolution(
            tmp_path,
            record,
            None,
            None,
            None,
        )

        assert isinstance(result, str)
        assert build_calls == []
    finally:
        reset_agent_effect_dependencies(token)
