from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.stalled_retained_hold import end_stalled_retained_hold
from tests.test_pending_merge_commit_recovery import _commit, _git

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    base = _commit(root, "shared.txt", "base\n")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    recorded = _commit(root, "feature.txt", "first\n")
    return root, base, recorded


def _integrating(root: Path, base: str, recorded: str) -> None:
    write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha=recorded,
            pre_target_sha=base,
            owning_worktree=str(root),
        ),
    )


def test_a_record_whose_feature_tip_moved_on_is_released(tmp_path: Path) -> None:
    root, base, recorded = _repo(tmp_path)
    _integrating(root, base, recorded)
    _commit(root, "feature.txt", "second\n")

    assert end_stalled_retained_hold(root, checks=5) is True
    assert read_record(root) is None


def test_a_record_whose_feature_tip_is_unchanged_is_kept(tmp_path: Path) -> None:
    root, base, recorded = _repo(tmp_path)
    _integrating(root, base, recorded)

    assert end_stalled_retained_hold(root, checks=5) is False
    assert read_record(root) is not None


def test_a_record_whose_feature_tip_was_rewritten_away_is_kept(tmp_path: Path) -> None:
    root, base, recorded = _repo(tmp_path)
    _integrating(root, base, recorded)
    assert _git(root, "reset", "--hard", base).returncode == 0
    _commit(root, "other.txt", "rewritten\n")

    assert end_stalled_retained_hold(root, checks=5) is False
    assert read_record(root) is not None


def test_a_superseded_record_is_released_on_the_first_check_without_waiting(
    tmp_path: Path,
) -> None:
    from ralph.pipeline.stalled_retained_hold import release_superseded_record

    root, base, recorded = _repo(tmp_path)
    _integrating(root, base, recorded)
    _commit(root, "feature.txt", "second\n")

    assert release_superseded_record(root) is True
    assert read_record(root) is None


def test_releasing_leaves_a_current_record_alone(tmp_path: Path) -> None:
    from ralph.pipeline.stalled_retained_hold import release_superseded_record

    root, base, recorded = _repo(tmp_path)
    _integrating(root, base, recorded)

    assert release_superseded_record(root) is False
    assert read_record(root) is not None
