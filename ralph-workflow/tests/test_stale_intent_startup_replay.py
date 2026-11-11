from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.git.git_run_result import GitRunResult

pytestmark = [pytest.mark.timeout_seconds(10)]


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(
        args,
        cwd=root,
        label="stale-intent-startup-replay-test",
        options=GitRunOptions(timeout=5),
    )


def _commit(root: Path, path: str, content: str) -> str:
    FsWorkspace(root).write(path, content)
    assert _git(root, "add", path).returncode == 0
    assert _git(root, "commit", "-m", path).returncode == 0
    return _git(root, "rev-parse", "HEAD").stdout.strip()


@functools.lru_cache(maxsize=4)
def _make_config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_enabled": True,
                "auto_integrate_remote_enabled": False,
            }
        }
    )


def test_startup_recovery_replays_a_stale_resolution_into_a_fresh_auto_rebase(
    tmp_git_repo: Path,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "base.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature work\n")
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=feature,
            pre_target_sha=base,
            resolving_rebase=True,
        ),
    )
    moved = _commit(root, "operator.txt", "operator work\n")
    assert moved != feature

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=_make_config(target),
    )

    assert outcome is not None
    assert not outcome.recovery_record_retained
    assert "lacks verified completion proof" not in (outcome.last_reason or "")
    assert read_record(root) is None
    backup_refs = _git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/ralph-backup/",
    ).stdout.split()
    assert backup_refs
    assert (
        _git(root, "rev-parse", target).stdout.strip()
        == _git(root, "rev-parse", "HEAD").stdout.strip()
    )
    assert _git(root, "show", f"{target}:feature.txt").stdout == "feature work\n"
    assert _git(root, "rev-list", "--merges", f"{base}..{target}").stdout.strip() == ""
    assert _git(root, "rev-list", "--count", f"{base}..{target}").stdout.strip() != "0"


def test_startup_recovery_replays_an_unresolved_integrating_record_with_a_moved_head(
    tmp_git_repo: Path,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "base.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature work\n")
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=feature,
            pre_target_sha=base,
        ),
    )
    moved = _commit(root, "operator.txt", "operator work\n")
    assert moved != feature

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=_make_config(target),
    )

    assert outcome is not None
    assert not outcome.recovery_record_retained
    assert "lacks verified completion proof" not in (outcome.last_reason or "")
    assert "unchanged feature tip" not in (outcome.last_reason or "")
    assert read_record(root) is None
    backup_refs = _git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/ralph-backup/",
    ).stdout.split()
    assert backup_refs
    assert _git(root, "show", f"{target}:operator.txt").stdout == "operator work\n"
    assert _git(root, "show", f"{target}:feature.txt").stdout == "feature work\n"
    assert _git(root, "rev-list", "--merges", f"{base}..{target}").stdout.strip() == ""
