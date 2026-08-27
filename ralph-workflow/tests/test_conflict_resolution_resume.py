"""Landed rebase stops survive a later failure via the progress sidecar.

The sidecar is scoped to ONE rebase, identified by the ``(orig-head,
onto)`` pair git pins for the whole replay. These tests cover both
directions of that scope: stops of the rebase in progress are kept (and
keep it off the abort path), while a record left by any other rebase is
discarded so the conflicted rebase is aborted normally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.conflict_resolution import rebase_loop as rebase_loop_module
from ralph.pipeline.conflict_resolution.progress import (
    RebaseResolutionProgress,
    clear_progress,
    load_progress,
    progress_path,
    save_progress,
)
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop, record_landed_stop

#: Identity of the rebase these tests pretend is paused in the worktree.
_FEATURE_SHA = "feature000000000000000000000000000000001"
_TARGET_SHA = "target0000000000000000000000000000000002"


def _stop(sha: str, index: int) -> RebaseStop:
    return RebaseStop(
        sha=sha,
        subject=f"stop {index}",
        conflicted_files=("src/alpha.py",),
        stop_index=index,
        stop_cap=5,
    )


def _pretend_rebase_paused(
    monkeypatch: pytest.MonkeyPatch,
    *,
    feature_sha: str | None = _FEATURE_SHA,
    target_sha: str | None = _TARGET_SHA,
) -> None:
    """Answer the rebase-identity probe without a real paused rebase."""
    monkeypatch.setattr(
        rebase_loop_module,
        "current_rebase_identity",
        lambda _root: (feature_sha, target_sha),
    )


def test_four_landed_stops_survive_in_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pretend_rebase_paused(monkeypatch)
    for index, sha in enumerate(("aaa1", "bbb2", "ccc3", "ddd4"), start=1):
        record_landed_stop(tmp_path, _stop(sha, index))
    progress = load_progress(tmp_path)
    assert progress is not None
    assert progress.landed_shas == ["aaa1", "bbb2", "ccc3", "ddd4"]
    assert progress_path(tmp_path).is_file()


def test_landed_stops_are_stamped_with_the_rebase_that_landed_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pretend_rebase_paused(monkeypatch)
    record_landed_stop(tmp_path, _stop("aaa1", 1))
    progress = load_progress(tmp_path)
    assert progress is not None
    assert progress.feature_sha == _FEATURE_SHA
    assert progress.target_sha == _TARGET_SHA


def test_landing_the_final_stop_removes_the_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A replay that finished has no remaining stops, so it keeps no record.

    ``git rebase --continue`` past the last stop leaves no rebase in
    progress, so there is no identity to stamp and nothing left to
    resume. Writing a record anyway is what outlived the rebase and
    wedged every later conflicted rebase in the same worktree.
    """
    _pretend_rebase_paused(monkeypatch)
    record_landed_stop(tmp_path, _stop("aaa1", 1))
    assert progress_path(tmp_path).is_file()

    _pretend_rebase_paused(monkeypatch, feature_sha=None, target_sha=None)
    record_landed_stop(tmp_path, _stop("bbb2", 2))

    assert not progress_path(tmp_path).exists()
    assert load_progress(tmp_path) is None


def test_resume_reads_landed_stops_after_a_fresh_process(tmp_path: Path) -> None:
    save_progress(
        tmp_path,
        RebaseResolutionProgress(
            landed_shas=["aaa1", "bbb2"],
            remaining_paths=["src/omega.py"],
            feature_sha=_FEATURE_SHA,
            target_sha=_TARGET_SHA,
        ),
    )
    reloaded = load_progress(tmp_path)
    assert reloaded is not None
    assert reloaded.landed_shas == ["aaa1", "bbb2"]
    assert reloaded.remaining_paths == ["src/omega.py"]


def _run_fallback_with_sidecar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    sidecar: RebaseResolutionProgress,
) -> list[Path]:
    """Drive one conflicted rebase to the fallback; return abort_rebase calls."""
    from ralph.git.merge import MergeResult
    from ralph.git.rebase.rebase import RebaseConflicts
    from ralph.pipeline import auto_integrate_rebase_merge as merge_module

    save_progress(tmp_path, sidecar)
    aborted: list[Path] = []

    def _abort(repo_root: Path) -> None:
        aborted.append(repo_root)
        clear_progress(repo_root)

    monkeypatch.setattr(
        merge_module, "current_rebase_identity", lambda _root: (_FEATURE_SHA, _TARGET_SHA)
    )
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
    monkeypatch.setattr(merge_module, "abort_rebase_discarding_progress", _abort)
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
    return aborted


def test_later_stop_failure_does_not_discard_landed_git_objects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Four landed replay commits must remain reachable when a later stop fails.

    Aborting the in-progress rebase throws those commits away even when the
    sidecar still names them. The fallback must not call abort_rebase.
    """
    landed = ["aaa1", "bbb2", "ccc3", "ddd4"]
    aborted = _run_fallback_with_sidecar(
        monkeypatch,
        tmp_path,
        sidecar=RebaseResolutionProgress(
            landed_shas=landed,
            remaining_paths=["src/omega.py"],
            feature_sha=_FEATURE_SHA,
            target_sha=_TARGET_SHA,
        ),
    )

    assert aborted == []
    progress = load_progress(tmp_path)
    assert progress is not None
    assert progress.landed_shas == landed


def test_sidecar_from_another_rebase_does_not_strand_this_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A record naming a DIFFERENT replay must not veto the abort.

    The wedging regression: a worktree whose first agent-resolved rebase
    left landed SHAs behind had every later conflicted rebase left
    paused on disk, waiting on a resume for stops the record does not
    describe. The stale record is discarded and the abort runs.
    """
    aborted = _run_fallback_with_sidecar(
        monkeypatch,
        tmp_path,
        sidecar=RebaseResolutionProgress(
            landed_shas=["olddead1", "olddead2"],
            remaining_paths=["src/ancient.py"],
            feature_sha="someotherfeature00000000000000000000009",
            target_sha="someothertarget000000000000000000000008",
        ),
    )

    assert aborted == [tmp_path]
    assert load_progress(tmp_path) is None


def test_unstamped_legacy_sidecar_does_not_strand_this_rebase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A record written before the identity existed belongs to no rebase."""
    aborted = _run_fallback_with_sidecar(
        monkeypatch,
        tmp_path,
        sidecar=RebaseResolutionProgress(
            landed_shas=["olddead1"],
            remaining_paths=["src/ancient.py"],
        ),
    )

    assert aborted == [tmp_path]
    assert load_progress(tmp_path) is None


def test_aborting_a_rebase_discards_its_progress_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The record must not outlive the abort that threw its commits away.

    Scoping cannot cover this case. ``git rebase --abort`` restores HEAD
    to ``orig-head``, so retrying the same rebase onto an unmoved target
    reproduces a byte-identical identity -- verified against real git --
    and the stale record passes the very check meant to reject it while
    the commits it names no longer exist.
    """
    from ralph.pipeline.conflict_resolution import abort as abort_module

    save_progress(
        tmp_path,
        RebaseResolutionProgress(
            landed_shas=["aaa1"],
            remaining_paths=["src/alpha.py"],
            feature_sha=_FEATURE_SHA,
            target_sha=_TARGET_SHA,
        ),
    )
    aborted: list[Path] = []

    def _record_abort(repo_root: Path) -> None:
        aborted.append(repo_root)

    monkeypatch.setattr(abort_module, "abort_rebase", _record_abort)

    abort_module.abort_rebase_discarding_progress(tmp_path)

    assert aborted == [tmp_path]
    assert not progress_path(tmp_path).exists()


def test_a_failed_abort_keeps_the_progress_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An abort that raised left the rebase in place, so its record still applies."""
    from ralph.pipeline.conflict_resolution import abort as abort_module

    save_progress(
        tmp_path,
        RebaseResolutionProgress(
            landed_shas=["aaa1"],
            remaining_paths=["src/alpha.py"],
            feature_sha=_FEATURE_SHA,
            target_sha=_TARGET_SHA,
        ),
    )

    def _raise(repo_root: Path) -> None:
        raise RuntimeError("abort refused")

    monkeypatch.setattr(abort_module, "abort_rebase", _raise)

    with pytest.raises(RuntimeError):
        abort_module.abort_rebase_discarding_progress(tmp_path)

    assert progress_path(tmp_path).is_file()
