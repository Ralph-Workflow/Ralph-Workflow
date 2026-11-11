from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._integration_continuation import promote_completed_resolution
from ralph.pipeline._owned_landing_reintegration import replay_stale_resolution
from ralph.pipeline.auto_integrate_backup_refs import create_stale_resolution_snapshot
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope
from tests.test_pending_merge_commit_recovery import _commit, _git

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _setup_stale_record(root: Path) -> tuple[str, str, str]:
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
    return target, base, feature


def test_stale_resolution_snapshot_keeps_staged_untracked_and_leaves_the_index_alone(
    tmp_git_repo: Path,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    _commit(root, "base.txt", "base\n")
    _commit(root, "dirty.txt", "committed content\n")
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "staged.txt").write_text("staged work\n", encoding="utf-8")
    assert _git(root, "add", "staged.txt").returncode == 0
    (root / "dirty.txt").write_text("dirty work\n", encoding="utf-8")
    (root / "untracked.txt").write_text("untracked work\n", encoding="utf-8")
    index_before = _git(root, "write-tree").stdout
    status_before = _git(root, "status", "--porcelain").stdout

    backup_ref, feature_sha = create_stale_resolution_snapshot(root, target, head, str(root))

    assert _git(root, "write-tree").stdout == index_before
    assert _git(root, "status", "--porcelain").stdout == status_before
    assert _git(root, "show", f"{backup_ref}:staged.txt").stdout == "staged work\n"
    assert _git(root, "show", f"{backup_ref}:untracked.txt").stdout == "untracked work\n"
    assert _git(root, "show", f"{backup_ref}:dirty.txt").stdout == "committed content\n"
    assert _git(root, "show", f"{backup_ref}^2:dirty.txt").stdout == "dirty work\n"
    assert feature_sha != head


@pytest.mark.parametrize("state", ["clean", "clean_without_config", "head_moved", "dirty"])
def test_stale_resolution_replay_preserves_work_and_retries_auto_rebase(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    root = tmp_git_repo
    target, _base, feature = _setup_stale_record(root)
    staged_path = root / "staged.txt"
    untracked_path = root / "untracked.txt"
    if state == "head_moved":
        moved = _commit(root, "operator.txt", "operator work\n")
        assert moved != feature
    elif state == "dirty":
        staged_path.write_text("staged work\n", encoding="utf-8")
        assert _git(root, "add", "staged.txt").returncode == 0
        staged_path.write_text("unstaged work\n", encoding="utf-8")
        untracked_path.write_text("untracked work\n", encoding="utf-8")

    calls: list[tuple[str, str | None]] = []
    switches: list[dict[str, object]] = []

    def fake_integrate_once_owned(
        _config: UnifiedConfig,
        _root: Path,
        target_name: str,
        _conflict_resolver: ConflictResolver | None,
        *,
        rebase_stop_resolver: RebaseStopResolver | None = None,
        owned_record: IntegrationRecord | None = None,
        **kwargs: object,
    ) -> tuple[RebaseState | None, bool]:
        del rebase_stop_resolver
        calls.append((target_name, owned_record.pre_feature_sha if owned_record else None))
        switches.append(kwargs)
        return RebaseState(last_outcome=RebaseOutcomeKind.REBASED, last_target=target_name), False

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._integrate_once_owned",
        fake_integrate_once_owned,
    )
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )
    stale = read_record(root)
    assert stale is not None

    outcome = replay_stale_resolution(
        root,
        stale,
        None if state == "clean_without_config" else config,
        None,
        None,
    )

    assert outcome is not None
    assert calls and calls[0][0] == target
    assert switches
    assert not switches[0].get("prefer_merge")
    assert not switches[0].get("force_endpoint_merge")
    assert "lacks verified completion proof" not in (outcome.last_reason or "")
    current = read_record(root)
    assert current is not None
    assert current.resolving_rebase is False
    assert current.resolving_merge is False
    refs = [
        line.strip()
        for line in _git(
            root,
            "for-each-ref",
            "--format=%(refname)",
            "refs/ralph-backup/",
        ).stdout.splitlines()
        if line.strip()
    ]
    assert len(refs) == 1
    backup_ref = refs[0]
    assert _git(root, "show", f"{backup_ref}:feature.txt").stdout == "feature work\n"
    if state == "dirty":
        assert _git(root, "show", f"{backup_ref}:staged.txt").stdout == "staged work\n"
        assert _git(root, "show", f"{backup_ref}:untracked.txt").stdout == "untracked work\n"
        assert _git(root, "show", f"{backup_ref}^2:staged.txt").stdout == "unstaged work\n"


def _recovery_config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )


def _backup_refs(root: Path) -> list[str]:
    return [
        line.strip()
        for line in _git(
            root,
            "for-each-ref",
            "--format=%(refname)",
            "refs/ralph-backup/",
        ).stdout.splitlines()
        if line.strip()
    ]


def test_recovery_releases_a_resolution_record_whose_work_already_landed(
    tmp_git_repo: Path,
) -> None:
    root = tmp_git_repo
    target, _base, _feature = _setup_stale_record(root)
    assert _git(root, "switch", target).returncode == 0
    assert _git(root, "merge", "--ff-only", "feature").returncode == 0

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=_recovery_config(target),
    )

    assert outcome is not None
    assert not outcome.recovery_record_retained
    assert "malformed integrated record" not in (outcome.last_reason or "")
    assert "lacks verified completion proof" not in (outcome.last_reason or "")
    assert read_record(root) is None
    assert _backup_refs(root) == []


def test_unresolved_integrating_record_with_moved_head_replays_into_auto_rebase(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    calls: list[tuple[str, str | None]] = []
    switches: list[dict[str, object]] = []

    def fake_integrate_once_owned(
        _config: UnifiedConfig,
        _root: Path,
        target_name: str,
        _conflict_resolver: ConflictResolver | None,
        *,
        rebase_stop_resolver: RebaseStopResolver | None = None,
        owned_record: IntegrationRecord | None = None,
        **kwargs: object,
    ) -> tuple[RebaseState | None, bool]:
        del rebase_stop_resolver
        calls.append((target_name, owned_record.pre_feature_sha if owned_record else None))
        switches.append(kwargs)
        return RebaseState(last_outcome=RebaseOutcomeKind.REBASED, last_target=target_name), False

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._integrate_once_owned",
        fake_integrate_once_owned,
    )

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=_recovery_config(target),
    )

    assert outcome is not None
    assert calls and calls[0][0] == target
    assert switches
    assert not switches[0].get("prefer_merge")
    assert not switches[0].get("force_endpoint_merge")
    assert "unchanged feature tip" not in (outcome.last_reason or "")
    assert "lacks verified completion proof" not in (outcome.last_reason or "")
    current = read_record(root)
    assert current is not None
    assert current.resolving_rebase is False
    assert current.resolving_merge is False
    refs = _backup_refs(root)
    assert len(refs) == 1
    assert _git(root, "show", f"{refs[0]}:feature.txt").stdout == "feature work\n"
    assert _git(root, "show", f"{refs[0]}:operator.txt").stdout == "operator work\n"


def test_promote_completed_resolution_names_a_leftover_operation_marker_instead_of_raising(
    tmp_git_repo: Path,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "file.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature work\n")
    assert _git(root, "switch", target).returncode == 0
    main_work = _commit(root, "file.txt", "conflicting main work\n")
    assert _git(root, "switch", "feature").returncode == 0
    _commit(root, "file.txt", "conflicting feature work\n")
    pick = _git(root, "cherry-pick", main_work)
    assert pick.returncode != 0
    assert (root / ".git" / "CHERRY_PICK_HEAD").exists()
    record = IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha=feature,
        pre_target_sha=base,
        resolving_rebase=True,
    )

    result = promote_completed_resolution(root, record)

    assert isinstance(result, str)
    assert "operation marker" in result
