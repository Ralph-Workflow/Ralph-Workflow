"""Tests for the bounded rebase resolve-and-continue loop.

The load-bearing contract is the DIVISION OF LABOUR: the resolver only
edits files, and Ralph alone stages, re-scans for surviving conflict
markers, asks git whether anything is still unmerged, and continues the
rebase. A resolver that reports success over a file still carrying
``<<<<<<<`` has resolved nothing, and ``git add`` clears a file's
unmerged bit even with the markers intact -- so the textual re-scan is
the only remaining proof and the loop must refuse to continue without it.

Every git seam is injected, so nothing here launches a process or touches
a repository and each test stays well inside the per-test timeout.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.rebase.rebase_continuation import (
    ConflictRemainingError,
    NoRebaseInProgressError,
    RebaseContinuationError,
)
from ralph.pipeline.conflict_resolution import rebase_loop as loop_module
from ralph.pipeline.conflict_resolution.graph import (
    MAX_REBASE_CONFLICT_STOPS,
    PHASE_RESOLUTION,
    TERMINAL_ABANDONED,
    TERMINAL_RESOLVED,
    route_after_stop,
)
from ralph.pipeline.conflict_resolution.rebase_loop import (
    RebaseStop,
    resolve_rebase_in_progress,
)

if TYPE_CHECKING:
    import pytest

_TARGET = "main"
_CONFLICTED = ["src/alpha.py"]


class _FakeRepo:
    """Scripted git state: N conflicted stops, then a finished rebase."""

    def __init__(self, stops: int, *, never_finishes: bool = False) -> None:
        self.remaining = stops
        self.never_finishes = never_finishes
        self.continue_calls = 0
        self.staged: list[list[str]] = []

    def in_progress(self, _root: Path) -> bool:
        return self.never_finishes or self.remaining > 0

    def continue_rebase(self, _root: Path) -> None:
        self.continue_calls += 1
        if not self.never_finishes:
            self.remaining -= 1


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    repo: _FakeRepo,
    *,
    surviving: Sequence[str] = (),
    unmerged: Sequence[str] = (),
    stage_ok: bool = True,
    verified: bool = True,
) -> None:
    """Replace every git call the loop makes with a scripted fake."""
    monkeypatch.setattr(loop_module, "rebase_in_progress_at", repo.in_progress)
    monkeypatch.setattr(
        loop_module, "get_conflicted_files", lambda **_kwargs: list(_CONFLICTED)
    )
    monkeypatch.setattr(
        loop_module, "_rev_parse_rebase_head", lambda _root: "abc1234"
    )
    monkeypatch.setattr(
        loop_module, "_rebase_head_subject", lambda _root: "feature edit"
    )

    def _stage(_root: Path, paths: Sequence[str]) -> bool:
        repo.staged.append(list(paths))
        return stage_ok

    monkeypatch.setattr(loop_module, "stage_paths", _stage)
    monkeypatch.setattr(
        loop_module, "paths_with_conflict_markers", lambda _r, _p: list(surviving)
    )
    monkeypatch.setattr(loop_module, "unmerged_paths", lambda _r: list(unmerged))
    monkeypatch.setattr(loop_module, "continue_rebase_at", repo.continue_rebase)
    monkeypatch.setattr(
        loop_module, "verify_rebase_completed_at", lambda _r, _t: verified
    )


def _accepting_resolver(seen: list[RebaseStop]):
    def _resolve(_root: Path, _target: str, stop: RebaseStop) -> bool:
        seen.append(stop)
        return True

    return _resolve


def test_single_stop_resolves_and_continues_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One conflicted commit: resolve, continue exactly once, report True."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))
        is True
    )
    assert repo.continue_calls == 1


def test_three_stops_drive_three_resolver_calls_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rebase stopping on three commits resolves each one in turn."""
    repo = _FakeRepo(stops=3)
    _install_seams(monkeypatch, repo)
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))
        is True
    )
    assert [stop.stop_index for stop in seen] == [1, 2, 3]


def test_declining_resolver_never_continues_the_rebase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A resolver that declines leaves the rebase exactly where it was."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)

    resolved = resolve_rebase_in_progress(
        tmp_path, _TARGET, lambda _root, _target, _stop: False
    )

    assert resolved is False
    assert repo.continue_calls == 0


def test_surviving_conflict_markers_reject_a_claimed_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ralph's re-scan overrules the resolver's own success claim."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, surviving=["src/alpha.py"])
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(
        tmp_path, _TARGET, _accepting_resolver(seen)
    )

    assert resolved is False
    assert repo.continue_calls == 0


def test_residual_unmerged_paths_reject_a_claimed_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """git's own unmerged-path answer is the second, independent gate."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, unmerged=["src/alpha.py"])
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(
        tmp_path, _TARGET, _accepting_resolver(seen)
    )

    assert resolved is False


def test_failure_to_stage_rejects_the_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Staging is Ralph's job, so a failed stage is Ralph's failure."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, stage_ok=False)
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(
        tmp_path, _TARGET, _accepting_resolver(seen)
    )

    assert resolved is False


def test_conflict_remaining_error_from_continue_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """git refusing to continue is a declined resolution, not a crash."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)

    def _raise(_root: Path) -> None:
        raise ConflictRemainingError("conflicts still exist in the index")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _raise)
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))
        is False
    )


def test_continuation_error_from_continue_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed ``git rebase --continue`` declines rather than propagating."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)

    def _raise(_root: Path) -> None:
        raise RebaseContinuationError("failed to continue rebase")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _raise)
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))
        is False
    )


def test_a_continue_that_lands_but_hits_the_next_conflict_is_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``rebase --continue`` exits non-zero when the NEXT commit conflicts.

    That is the stop advancing, not the stop failing. Reading it as a
    failure made every multi-conflict branch fall back to the endpoint
    merge while single-conflict branches resolved cleanly.
    """
    repo = _FakeRepo(stops=2)
    _install_seams(monkeypatch, repo)
    heads = iter(["sha-one", "sha-two"])
    monkeypatch.setattr(
        loop_module, "_rev_parse_rebase_head", lambda _root: next(heads, "sha-two")
    )

    def _continue_then_conflict(root: Path) -> None:
        repo.continue_rebase(root)
        if repo.remaining > 0:
            raise RebaseContinuationError("could not apply the next commit")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _continue_then_conflict)
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))
        is True
    )


def test_a_continue_that_fails_on_the_same_stop_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unchanged REBASE_HEAD means nothing landed: decline, do not loop."""
    repo = _FakeRepo(stops=1, never_finishes=True)
    _install_seams(monkeypatch, repo)

    def _raise(_root: Path) -> None:
        raise RebaseContinuationError("hook rejected the commit")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _raise)
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))
        is False
    )
    assert len(seen) == 1


def test_rebase_finishing_during_continue_is_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``NoRebaseInProgressError`` means the replay finished, not failed."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    calls: list[int] = []

    def _finish(_root: Path) -> None:
        calls.append(1)
        repo.remaining = 0
        raise NoRebaseInProgressError("no rebase in progress")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _finish)
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))
        is True
    )


def test_a_rebase_that_never_finishes_exhausts_the_stop_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The loop terminates at MAX_REBASE_CONFLICT_STOPS rather than spinning."""
    repo = _FakeRepo(stops=0, never_finishes=True)
    _install_seams(monkeypatch, repo)
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(
        tmp_path, _TARGET, _accepting_resolver(seen)
    )

    assert resolved is False
    assert len(seen) == MAX_REBASE_CONFLICT_STOPS


def test_a_paused_rebase_with_no_conflicted_path_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing to resolve means hand the repository back untouched."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(loop_module, "get_conflicted_files", lambda **_kwargs: [])
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))
        is False
    )


def test_an_unexpected_exception_declines_without_propagating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The never-raises contract the integration step depends on."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)

    def _explode(**_kwargs: object) -> list[str]:
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(loop_module, "get_conflicted_files", _explode)

    assert (
        resolve_rebase_in_progress(
            tmp_path, _TARGET, lambda _root, _target, _stop: True
        )
        is False
    )


def test_an_unverifiable_completion_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HEAD not descending from the target is not a resolved rebase."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, verified=False)
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))
        is False
    )


def test_the_stop_carries_the_replayed_commit_and_the_conflicted_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The resolver's whole context: which commit, which files, where in the loop."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    seen: list[RebaseStop] = []

    resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert seen == [
        RebaseStop(
            sha="abc1234",
            subject="feature edit",
            conflicted_files=("src/alpha.py",),
            stop_index=1,
            stop_cap=MAX_REBASE_CONFLICT_STOPS,
        )
    ]


def test_route_after_stop_resolves_continues_and_abandons() -> None:
    """The pure router hits all three terminals at its boundaries."""
    assert route_after_stop(1, True) == TERMINAL_RESOLVED
    assert route_after_stop(1, False) == PHASE_RESOLUTION
    assert route_after_stop(MAX_REBASE_CONFLICT_STOPS, False) == TERMINAL_ABANDONED
