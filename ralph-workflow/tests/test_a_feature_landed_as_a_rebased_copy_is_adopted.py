from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.stalled_retained_hold import end_stalled_retained_hold
from tests.test_pending_merge_commit_recovery import _commit, _git

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _landed_elsewhere(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    _commit(root, "notes.txt", "notes\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature\n")
    assert _git(root, "switch", "main").returncode == 0
    _commit(root, "sibling.txt", "sibling\n")
    assert _git(root, "cherry-pick", feature).returncode == 0
    landed = _head(root)
    _commit(root, "later.txt", "later\n")
    assert _git(root, "switch", "feature").returncode == 0
    (root / "notes.txt").write_text("uncommitted edit\n", encoding="utf-8")
    return root, feature, landed


def _record(root: Path, feature: str, integrated: str) -> None:
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha=feature,
            pre_target_sha=integrated,
            integrated_feature_sha=integrated,
            owning_worktree=str(root),
        ),
    )


def test_a_feature_whose_rebased_copy_is_on_the_target_moves_to_it(tmp_path: Path) -> None:
    root, feature, landed = _landed_elsewhere(tmp_path)
    _record(root, feature, landed)

    assert end_stalled_retained_hold(root, checks=6) is True

    assert read_record(root) is None
    assert _head(root) == landed
    assert (root / "notes.txt").read_text(encoding="utf-8") == "uncommitted edit\n"


def test_a_feature_without_a_copy_on_the_target_keeps_its_commit(tmp_path: Path) -> None:
    root, feature, landed = _landed_elsewhere(tmp_path)
    sibling = _git(root, "rev-parse", f"{landed}~1").stdout.strip()
    _record(root, feature, sibling)

    assert end_stalled_retained_hold(root, checks=6) is False

    assert read_record(root) is not None
    assert _head(root) == feature


def test_a_landed_copy_is_adopted_on_the_first_check_before_any_diagnosis(
    tmp_path: Path,
) -> None:
    from ralph.pipeline.stalled_retained_hold import release_superseded_record

    root, feature, landed = _landed_elsewhere(tmp_path)
    _record(root, feature, landed)

    assert release_superseded_record(root) is True

    assert read_record(root) is None
    assert _head(root) == landed
    assert (root / "notes.txt").read_text(encoding="utf-8") == "uncommitted edit\n"
