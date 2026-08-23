"""Landed rebase stops survive a later failure via the progress sidecar."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution.progress import (
    RebaseResolutionProgress,
    load_progress,
    progress_path,
    save_progress,
)
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop, record_landed_stop

if TYPE_CHECKING:
    import pytest


def test_four_landed_stops_survive_in_the_sidecar(tmp_path: Path) -> None:
    for index, sha in enumerate(("aaa1", "bbb2", "ccc3", "ddd4"), start=1):
        record_landed_stop(
            tmp_path,
            RebaseStop(
                sha=sha,
                subject=f"stop {index}",
                conflicted_files=("src/alpha.py",),
                stop_index=index,
                stop_cap=5,
            ),
        )
    progress = load_progress(tmp_path)
    assert progress is not None
    assert progress.landed_shas == ["aaa1", "bbb2", "ccc3", "ddd4"]
    assert progress_path(tmp_path).is_file()


def test_resume_reads_landed_stops_after_a_fresh_process(tmp_path: Path) -> None:
    save_progress(
        tmp_path,
        RebaseResolutionProgress(landed_shas=["aaa1", "bbb2"], remaining_paths=["src/omega.py"]),
    )
    reloaded = load_progress(tmp_path)
    assert reloaded is not None
    assert reloaded.landed_shas == ["aaa1", "bbb2"]
    assert reloaded.remaining_paths == ["src/omega.py"]


def test_later_stop_failure_does_not_discard_landed_git_objects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Four landed replay commits must remain reachable when a later stop fails.

    Aborting the in-progress rebase throws those commits away even when the
    sidecar still names them. The fallback must not call abort_rebase.
    """
    from ralph.git.merge import MergeResult
    from ralph.git.rebase.rebase import RebaseConflicts
    from ralph.pipeline import auto_integrate_rebase_merge as merge_module

    landed = ["aaa1", "bbb2", "ccc3", "ddd4"]
    reachable = dict.fromkeys(landed, "commit")
    save_progress(
        tmp_path,
        RebaseResolutionProgress(landed_shas=landed, remaining_paths=["src/omega.py"]),
    )
    aborted: list[Path] = []

    def _abort(repo_root: Path | None = None, **_kwargs: object) -> None:
        aborted.append(repo_root or tmp_path)
        reachable.clear()

    monkeypatch.setattr(merge_module, "_range_routing_reason", lambda _root, _target: None)
    monkeypatch.setattr(
        merge_module,
        "rebase_onto",
        lambda _target, repo_root: RebaseConflicts(files=["src/omega.py"]),
    )
    monkeypatch.setattr(merge_module, "set_resolving_rebase", lambda *_args: True)
    monkeypatch.setattr(
        merge_module, "resolve_rebase_in_progress", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(merge_module, "rebase_in_progress", lambda _root: True)
    monkeypatch.setattr(merge_module, "abort_rebase", _abort)
    monkeypatch.setattr(
        merge_module,
        "endpoint_merge_with_resolution",
        lambda *_args, **_kwargs: MergeResult(outcome="conflict"),
    )

    merge_module.run_rebase_or_merge(
        tmp_path,
        "main",
        None,
        rebase_stop_resolver=lambda *_args: False,
    )

    assert aborted == []
    assert reachable == dict.fromkeys(landed, "commit")
    progress = load_progress(tmp_path)
    assert progress is not None
    assert progress.landed_shas == landed
