"""Startup recovery must replay a stale resolution into a fresh automatic rebase.

The case in this module uses real Git because the protected artifact is a
Git object: the durable integration record claims a resolution that Git
disproves, so the recovery has to observe the real repository state,
preserve the work in a backup ref and land the feature with an automatic
rebase. The helpers are local to this module on purpose: the file must
stay free of white-box coupling to another test module's privates.
"""

from __future__ import annotations

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
    """Run one real git command in ``root`` through the public git runner."""
    return run_git(
        args,
        cwd=root,
        label="stale-intent-startup-replay-test",
        options=GitRunOptions(timeout=5),
    )


def _commit(root: Path, path: str, content: str) -> str:
    """Write ``content`` to ``path`` and commit it; return the new commit SHA."""
    FsWorkspace(root).write(path, content)
    assert _git(root, "add", path).returncode == 0
    assert _git(root, "commit", "-m", path).returncode == 0
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _make_config(target: str) -> UnifiedConfig:
    """Return the auto-integrate config that lands ``target``."""
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
    """A retained record Git disproves is replayed at startup, never withheld.

    The symptom state: the durable record claims "resolving rebase",
    Git shows NO rebase and NO merge, the work is not contained in the
    target, and HEAD has moved on. The record is stale intent, so the
    startup recovery must observe Git, preserve the work in a backup
    ref, drop the stale flags and re-enter at step 1 (the automatic
    rebase) instead of withholding the landing or deferring the
    catch-up.
    """
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
    # HEAD moved on: the record's resolution claim is not what Git shows.
    moved = _commit(root, "operator.txt", "operator work\n")
    assert moved != feature

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=_make_config(target),
    )

    assert outcome is not None
    # The startup catch-up is NOT deferred: recovery did not come back
    # still owning the durable record.
    assert not outcome.recovery_record_retained
    assert "lacks verified completion proof" not in (outcome.last_reason or "")
    # The stale flags are gone and the record released.
    assert read_record(root) is None
    # The work was preserved in a backup ref.
    backup_refs = _git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/ralph-backup/",
    ).stdout.split()
    assert backup_refs
    # A FRESH automatic rebase ran and the feature landed on the target.
    assert (
        _git(root, "rev-parse", target).stdout.strip()
        == _git(root, "rev-parse", "HEAD").stdout.strip()
    )
    assert _git(root, "show", f"{target}:feature.txt").stdout == "feature work\n"
    # The rebase was the FIRST attempt: the replay landed the feature
    # commits, and NO merge commit was created on the way there. An
    # endpoint merge would have left one behind.
    assert _git(root, "rev-list", "--merges", f"{base}..{target}").stdout.strip() == ""
    assert _git(root, "rev-list", "--count", f"{base}..{target}").stdout.strip() != "0"


def test_startup_recovery_replays_an_unresolved_integrating_record_with_a_moved_head(
    tmp_git_repo: Path,
) -> None:
    """An integrating record Git disproves re-enters at the automatic rebase.

    Variant of the symptom state: the record claims "integrating" (no
    resolver flags), Git shows NO operation, the work is not contained in
    the target, and HEAD moved on.  The previous escape hatch only fired
    when HEAD equalled the recorded pre-feature SHA with a clean tree;
    this state retained with OWNED_TARGET_PRECONDITION_FAILED on every
    startup and deferred the catch-up forever.  Stale intent must replay:
    observe Git, save the work to a backup ref, drop the stale identity
    and re-enter at step 1.
    """
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
    # HEAD moved on: the recorded pre-feature identity is unrecoverable.
    moved = _commit(root, "operator.txt", "operator work\n")
    assert moved != feature

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=_make_config(target),
    )

    assert outcome is not None
    # The startup catch-up is NOT deferred: recovery did not come back
    # still owning the durable record.
    assert not outcome.recovery_record_retained
    assert "lacks verified completion proof" not in (outcome.last_reason or "")
    assert "unchanged feature tip" not in (outcome.last_reason or "")
    # The stale identity is gone and the record released.
    assert read_record(root) is None
    # The moved HEAD work was preserved in a backup ref.
    backup_refs = _git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/ralph-backup/",
    ).stdout.split()
    assert backup_refs
    # A FRESH automatic rebase ran and the feature landed on the target:
    # the operator work is in the target and NO merge commit was created
    # on the way there.
    assert _git(root, "show", f"{target}:operator.txt").stdout == "operator work\n"
    assert _git(root, "show", f"{target}:feature.txt").stdout == "feature work\n"
    assert _git(root, "rev-list", "--merges", f"{base}..{target}").stdout.strip() == ""
