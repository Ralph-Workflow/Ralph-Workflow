from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from git import Repo
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline import run_loop
from ralph.pipeline.auto_integrate_backup_refs import (
    create_rebase_backup_ref,
    qualified_backup_ref,
)
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery_terminal import write_resolution_terminal_marker
from ralph.pipeline.no_progress_hold import (
    NO_PROGRESS_RELEASE_REPEATS,
    NO_PROGRESS_REPEATS,
    NoProgressHoldBackoff,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.recovery.controller import RecoveryController
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.subprocess_e2e

_TICKS = 10


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(root: Path, relative: str, content: str, message: str) -> str:
    (root / relative).write_text(content, encoding="utf-8")
    repo = Repo(root)
    try:
        repo.index.add([relative])
        return repo.index.commit(message).hexsha
    finally:
        repo.close()


def _stale_integrated_record(tmp_git_repo: Path, tmp_path: Path) -> tuple[str, Path, str]:
    target = _git(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD").removeprefix("refs/heads/")
    pre_feature = _commit(tmp_git_repo, "a.txt", "a\n", "feature work")
    integrated = _commit(tmp_git_repo, "b.txt", "b\n", "target before landing")
    _commit(tmp_git_repo, "c.txt", "c\n", "target after landing")
    feature = tmp_path / "feature"
    _git(tmp_git_repo, "worktree", "add", "-b", "feature", str(feature))
    write_record(
        feature,
        IntegrationRecord(
            phase="integrated",
            target=target,
            pre_feature_sha=pre_feature,
            pre_target_sha=integrated,
            integrated_feature_sha=integrated,
            operation_kind="feature_integrate",
            owning_worktree=None,
            reintegrate_pending=False,
            merge_commit_repair_attempts=1,
            repair_last_error=(
                "operation completion proof failed: record.owning_worktree is missing or empty"
            ),
        ),
    )
    create_rebase_backup_ref(feature, pre_feature)
    return target, feature, pre_feature


def _assert_released_and_planning_proceeds(
    feature: Path, target: str, pre_feature: str
) -> list[RebaseState | None]:
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )
    logs: list[str] = []
    sink = logger.add(lambda message: logs.append(str(message)), level="INFO", format="{message}")
    try:
        outcomes = [
            integrate_before_planning(config, WorkspaceScope(feature), RebaseState())
            for _tick in range(_TICKS)
        ]
    finally:
        logger.remove(sink)

    assert read_record(feature) is None
    assert not [line for line in logs if "landing withheld" in line]
    assert not [line for line in logs if "integration has not landed" in line]
    assert not any(outcome.recovery_record_retained for outcome in outcomes if outcome)
    backup = qualified_backup_ref(str(feature), pre_feature)
    assert _git(feature, "for-each-ref", backup) == ""
    ctx = SimpleNamespace(
        config=config,
        workspace_scope=WorkspaceScope(feature),
        policy_bundle=cached_default_policy_bundle(),
        active_display=None,
    )
    _state, held = run_loop.ensure_planning_in_sync(
        PipelineState(phase="planning", rebase=outcomes[-1] or RebaseState()), ctx, "planning"
    )
    assert held is None
    return outcomes


@pytest.mark.timeout_seconds(60)
def test_a_stale_integrated_record_already_contained_in_the_target_is_released(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    target, feature, pre_feature = _stale_integrated_record(tmp_git_repo, tmp_path)
    before = branch_sha(tmp_git_repo, target)

    _assert_released_and_planning_proceeds(feature, target, pre_feature)

    assert branch_sha(tmp_git_repo, target) == before


@pytest.mark.timeout_seconds(60)
def test_a_stale_integrated_record_is_released_when_the_feature_moved_past_it(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    target, feature, pre_feature = _stale_integrated_record(tmp_git_repo, tmp_path)
    new_work = _commit(feature, "d.txt", "d\n", "new feature work")

    _assert_released_and_planning_proceeds(feature, target, pre_feature)

    assert _git(feature, "rev-parse", "HEAD") == new_work


@pytest.mark.timeout_seconds(90)
def test_a_terminal_retained_hold_that_never_progresses_ends_instead_of_waiting_forever(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    target = _git(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD").removeprefix("refs/heads/")
    pre_feature = _commit(tmp_git_repo, "a.txt", "a\n", "feature work")
    feature = tmp_path / "feature"
    _git(tmp_git_repo, "worktree", "add", "-b", "feature", str(feature))
    unrelated = _git(feature, "commit-tree", "HEAD^{tree}", "-m", "never landed")
    record = IntegrationRecord(
        phase="integrated",
        target=target,
        pre_feature_sha=pre_feature,
        pre_target_sha=pre_feature,
        integrated_feature_sha=unrelated,
        operation_kind="feature_integrate",
    )
    write_record(feature, record)
    write_resolution_terminal_marker(feature, record)
    create_rebase_backup_ref(feature, pre_feature)
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": target}}
    )
    waits: list[float] = []
    ctx = SimpleNamespace(
        config=config,
        workspace_scope=WorkspaceScope(feature),
        policy_bundle=cached_default_policy_bundle(),
        active_display=None,
        retained_hold_backoff=NoProgressHoldBackoff(),
        sleep=waits.append,
        pipeline_deps=MagicMock(),
        registry={},
        display_context=None,
        controller=RecoveryController(),
    )
    state = PipelineState(phase="planning")
    holds = 0
    held = run_loop.block_unresolved_integration(state, ctx, "planning")
    while held is not None and holds <= NO_PROGRESS_RELEASE_REPEATS:
        holds += 1
        held = run_loop.block_unresolved_integration(held[0], ctx, "planning")

    assert held is None
    assert holds == NO_PROGRESS_RELEASE_REPEATS - 1
    assert read_record(feature) is None
    assert len(waits) == NO_PROGRESS_RELEASE_REPEATS - NO_PROGRESS_REPEATS
    backup = qualified_backup_ref(str(feature), pre_feature)
    assert _git(feature, "for-each-ref", "--format=%(objectname)", backup) == pre_feature
