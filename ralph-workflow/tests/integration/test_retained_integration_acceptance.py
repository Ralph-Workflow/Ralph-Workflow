from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.git.git_run_result import GitRunResult
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(
        args,
        cwd=root,
        label="retained-integration-acceptance",
        options=GitRunOptions(timeout=5),
    )


def _commit(root: Path, path: str, content: str) -> str:
    (root / path).write_text(content, encoding="utf-8")
    assert _git(root, "add", path).returncode == 0
    assert _git(root, "commit", "-m", path).returncode == 0
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def test_acceptance_scenario_1_linked_worktree_unrelated_branch_advance(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    """Scenario 1: Advance unrelated branch in linked worktree while retained obligation exists.

    In linked worktrees, advance an unrelated branch while another worktree has
    a retained integration obligation. The obligation must remain valid and make
    progress according to its own state.
    """
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")

    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature content\n")

    # Retained integration obligation in root worktree
    write_record(
        root,
        IntegrationRecord(
            phase="integrated",
            target=target,
            pre_feature_sha=feature,
            pre_target_sha=base,
            integrated_feature_sha=feature,
        ),
    )

    # Create linked worktree with unrelated branch
    other_worktree = tmp_path / "other-worktree"
    assert (
        _git(root, "worktree", "add", "-b", "unrelated-branch", str(other_worktree)).returncode == 0
    )
    _commit(other_worktree, "unrelated.txt", "unrelated commit 1\n")
    _commit(other_worktree, "unrelated.txt", "unrelated commit 2\n")

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )

    # In root worktree, recover integration; must progress and land despite unrelated branch advances
    outcome = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert (
        _git(root, "rev-parse", target).stdout.strip()
        == _git(root, "rev-parse", feature).stdout.strip()
    )
    assert read_record(root) is None


def test_acceptance_scenario_2_target_moved_during_integration(
    tmp_git_repo: Path,
) -> None:
    """Scenario 2: Move configured target during integration.

    Ownership must remain and the next action must become reintegration
    against the new target.
    """
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")

    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")

    write_record(
        root,
        IntegrationRecord(
            phase="integrated",
            target=target,
            pre_feature_sha=feature,
            pre_target_sha=base,
            integrated_feature_sha=feature,
        ),
    )

    # Target moves while integration record exists
    assert _git(root, "switch", target).returncode == 0
    _commit(root, "shared.txt", "target moved\n")
    assert _git(root, "switch", "feature").returncode == 0

    record = read_record(root)
    assert record is not None

    # Next action must be reintegrating against target, not landing or completion
    action = next_integration_action(record.model_copy(update={"target_moved": True}))
    assert action == IntegrationAction.REINTEGRATE_AGAINST_TARGET

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )

    # Reintegrating against target keeps ownership and blocks dispatch until resolved
    declined = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        rebase_stop_resolver=lambda _root, _target, _stop: False,
        conflict_resolver=lambda _root, _target: False,
    )
    assert declined is not None and not declined.fast_forwarded
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed

    # Resolve conflict to finish reintegration and landing
    def resolve(repo: Path, _branch: str, _stop: RebaseStop) -> bool:
        (repo / "shared.txt").write_text("feature and target\n", encoding="utf-8")
        return True

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        rebase_stop_resolver=resolve,
    )
    assert outcome is not None and outcome.fast_forwarded is True
    assert read_record(root) is None


def test_acceptance_scenario_5_conflict_resolved_landing_pending(
    tmp_git_repo: Path,
) -> None:
    """Scenario 5: Conflict resolution completed, target landing pending.

    Ralph must continue the integration lifecycle rather than re-enter development.
    """
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")

    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature content\n")

    # Conflict was resolved into a rebased/merged feature, but target hasn't landed
    record = IntegrationRecord(
        phase="integrated",
        target=target,
        pre_feature_sha=feature,
        pre_target_sha=base,
        integrated_feature_sha=feature,
        landing_pending=True,
    )
    write_record(root, record)

    # Next action must be LAND_TARGET
    action = next_integration_action(record)
    assert action == IntegrationAction.LAND_TARGET

    # Resolution inspection must show dispatch is blocked and recoverable
    rebase_state = RebaseState(
        last_outcome=RebaseOutcomeKind.MERGED,
        fast_forwarded=False,
    )
    resolution = inspect_integration_resolution(root, rebase_state)
    assert resolution.dispatch_allowed is False
    assert resolution.status.name == "RECOVERABLE"

    # Terminal authority must forbid terminal success while landing is pending
    assert is_terminal_success_eligible(None, record, rebase_state) is False

    # Integration lifecycle continues to land target
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )
    outcome = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert outcome is not None and outcome.fast_forwarded is True
    assert read_record(root) is None
    assert (
        _git(root, "rev-parse", target).stdout.strip()
        == _git(root, "rev-parse", feature).stdout.strip()
    )
