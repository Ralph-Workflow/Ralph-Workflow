"""Landed rebase stops survive a later failure via the progress sidecar.

The sidecar is scoped to ONE rebase, identified by the ``(orig-head,
onto)`` pair git pins for the whole replay. These tests cover both
directions of that scope: stops of the rebase in progress are kept (and
keep it off the abort path), while a record left by another rebase is
discarded so the current unresolved stop still reaches its next agent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.conflict_resolution import rebase_loop as rebase_loop_module
from ralph.pipeline.conflict_resolution.progress import (
    RebaseResolutionProgress,
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

    def identity(_root: Path) -> tuple[str | None, str | None]:
        return feature_sha, target_sha

    monkeypatch.setattr(rebase_loop_module, "current_rebase_identity", identity)


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


@pytest.mark.parametrize("identity", ["current", "foreign", "legacy"])
def test_interrupted_resolution_resumes_current_stop_without_aborting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    identity: str,
) -> None:
    """Sidecar scope controls skipped stops; an interrupted agent never destroys this replay."""
    from ralph.git.rebase.rebase import RebaseConflicts
    from ralph.pipeline import auto_integrate_rebase_merge as merge_module
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

    current = identity == "current"
    sidecar = RebaseResolutionProgress(
        landed_shas=["already-landed"] if current else ["pending-stop"],
        remaining_paths=["src/alpha.py"],
        feature_sha=_FEATURE_SHA
        if current
        else ("foreign-feature" if identity == "foreign" else None),
        target_sha=_TARGET_SHA
        if current
        else ("foreign-target" if identity == "foreign" else None),
    )
    save_progress(tmp_path, sidecar)
    events: list[str] = []
    paused = [True]
    _install_current_stop_seams(monkeypatch, paused, events)

    def rebase(_target: str, *, repo_root: Path) -> RebaseConflicts:
        return RebaseConflicts(files=["src/alpha.py"])

    def record(_root: Path) -> IntegrationRecord:
        return IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha=_FEATURE_SHA,
            pre_target_sha=_TARGET_SHA,
            resolving_rebase=True,
        )

    def abort(_root: Path) -> None:
        events.append("abort")

    def endpoint(*_args: object, **_kwargs: object) -> None:
        events.append("endpoint merge")

    monkeypatch.setattr(merge_module, "_range_routing_reason", _ignore)
    monkeypatch.setattr(merge_module, "rebase_onto", rebase)
    monkeypatch.setattr(merge_module, "set_resolving_rebase", _succeed)
    monkeypatch.setattr(merge_module, "read_record", record)
    monkeypatch.setattr(merge_module, "abort_rebase_discarding_progress", abort)
    monkeypatch.setattr(merge_module, "endpoint_merge_with_resolution", endpoint)

    def interrupted(_root: Path, _target: str, stop: RebaseStop) -> bool:
        assert stop.sha == "pending-stop"
        events.append("first agent partial work")
        return False

    result = merge_module.run_rebase_or_merge(
        tmp_path, "main", None, rebase_stop_resolver=interrupted
    )
    assert result.short_circuit is not None and result.short_circuit.recovery_record_retained
    assert "progress retained" in (result.short_circuit.last_reason or "")
    assert not result.merge_attempted
    progress = load_progress(tmp_path)
    if current:
        assert progress is not None and progress.landed_shas == ["already-landed"]
    else:
        assert progress is None

    def next_agent(_root: Path, _target: str, stop: RebaseStop) -> bool:
        assert stop.sha == "pending-stop"
        assert events == ["first agent partial work"]
        events.append("next agent completed current stop")
        return True

    assert rebase_loop_module.resolve_rebase_in_progress(tmp_path, "main", next_agent)
    assert events == ["first agent partial work", "next agent completed current stop", "continue"]
    assert not paused[0]
    assert load_progress(tmp_path) is None


def _ignore(*_args: object, **_kwargs: object) -> None:
    return None


def _succeed(*_args: object, **_kwargs: object) -> bool:
    return True


def _decline(*_args: object, **_kwargs: object) -> bool:
    return False


def _install_current_stop_seams(
    monkeypatch: pytest.MonkeyPatch, paused: list[bool], events: list[str]
) -> None:
    """Inject Git observations while exercising the real resolution loop and sidecar I/O."""
    loop = rebase_loop_module

    def identity(_root: Path) -> tuple[str | None, str | None]:
        return (_FEATURE_SHA, _TARGET_SHA) if paused[0] else (None, None)

    def in_progress(_root: Path) -> bool:
        return paused[0]

    def base(_root: Path) -> str:
        return _TARGET_SHA

    def stop(_root: Path, index: int, _cap: int) -> RebaseStop:
        return _stop("pending-stop", index)

    def dirty(_root: Path) -> frozenset[str]:
        return frozenset({"src/alpha.py"})

    def completed(_root: Path, _target: str) -> bool:
        return not paused[0]

    monkeypatch.setattr(loop, "current_rebase_identity", identity)
    monkeypatch.setattr(loop, "rebase_in_progress_at", in_progress)
    monkeypatch.setattr(loop, "_rebase_base_sha", base)
    monkeypatch.setattr(loop, "_is_at_the_first_replay", _decline)
    monkeypatch.setattr(loop, "_read_stop", stop)
    monkeypatch.setattr(loop, "_worktree_dirty_paths", dirty)
    monkeypatch.setattr(loop, "_try_deterministic_resolution", _decline)
    monkeypatch.setattr(loop, "_stage_and_prove", _succeed)
    monkeypatch.setattr(loop, "_remove_ort_residue", _succeed)
    monkeypatch.setattr(loop, "prepare_pending_rebase", _ignore)
    monkeypatch.setattr(loop, "finish_pending_rebase", _ignore)
    monkeypatch.setattr(loop, "verify_rebase_completed_at", completed)

    def continue_rebase(_root: Path, *, skip_empty: bool = True) -> None:
        events.append("continue")
        paused[0] = False

    monkeypatch.setattr(loop, "continue_rebase_at", continue_rebase)


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
