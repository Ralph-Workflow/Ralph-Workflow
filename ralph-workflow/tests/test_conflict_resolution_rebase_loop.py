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

from ralph.git.git_run_result import GitRunResult
from ralph.git.rebase.rebase_continuation import (
    ConflictRemainingError,
    NoRebaseInProgressError,
    RebaseContinuationError,
)
from ralph.pipeline.conflict_resolution import rebase_loop as loop_module
from ralph.pipeline.conflict_resolution import status as status_module
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
#: The commit a paused rebase is replaying onto, as git records it in
#: ``rebase-merge/onto``. Completion is proved against THIS, not against
#: the target name a fleet sibling can move mid-resolution.
_BASE_SHA = "ba5e0000000000000000000000000000000000ba"


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
    dirty_after_resolution: Sequence[str] | None = None,
    dirty_query_fails: bool = False,
    replay_progress: tuple[int, int] | None = None,
) -> None:
    """Replace every git call the loop makes with a scripted fake.

    ``dirty_after_resolution`` scripts what the worktree looks like once
    the resolver has run. The loop reads the worktree twice per stop --
    once before the resolver, once after -- so the fake alternates, and
    leaving the argument ``None`` means the resolver touched exactly the
    conflicted paths it was given.
    """
    _install_worktree_seam(
        monkeypatch,
        dirty_after_resolution=dirty_after_resolution,
        dirty_query_fails=dirty_query_fails,
    )
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
    monkeypatch.setattr(loop_module, "_rebase_base_sha", lambda _root: _BASE_SHA)
    monkeypatch.setattr(
        loop_module, "_read_replay_progress", lambda _root: replay_progress
    )
    monkeypatch.setattr(
        loop_module, "verify_rebase_completed_at", lambda _r, _t: verified
    )


def _install_worktree_seam(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dirty_after_resolution: Sequence[str] | None,
    dirty_query_fails: bool,
) -> None:
    """Script the before/after worktree observations of one stop."""
    reads = {"count": 0}

    def _dirty_paths(_root: Path) -> frozenset[str] | None:
        if dirty_query_fails:
            return None
        reads["count"] += 1
        is_before_read = reads["count"] % 2 == 1
        if is_before_read or dirty_after_resolution is None:
            return frozenset(_CONFLICTED)
        return frozenset(dirty_after_resolution)

    monkeypatch.setattr(loop_module, "_worktree_dirty_paths", _dirty_paths)


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


def test_a_resolver_that_edits_an_unrequested_path_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Scope is enforced, not merely requested.

    Only ``stop.conflicted_files`` is staged, so an edit anywhere else
    cannot reach the replayed commit -- it can only linger as dirty
    worktree state on top of a rebase that claimed to have landed. The
    stop is refused before anything is staged.
    """
    repo = _FakeRepo(stops=1)
    _install_seams(
        monkeypatch,
        repo,
        dirty_after_resolution=[*_CONFLICTED, "docs/unrelated.md"],
    )
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(
        tmp_path, _TARGET, _accepting_resolver(seen)
    )

    assert resolved is False
    assert repo.staged == []
    assert repo.continue_calls == 0


def test_a_worktree_dirty_before_the_resolver_ran_is_not_blamed_on_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gate is about what THIS round changed, not what it inherited."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    # Constant across both reads: the same unrelated path is dirty before
    # AND after, so it was not this round's doing.
    monkeypatch.setattr(
        loop_module,
        "_worktree_dirty_paths",
        lambda _root: frozenset([*_CONFLICTED, "docs/already-dirty.md"]),
    )
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))
        is True
    )


def test_an_unreadable_worktree_declines_rather_than_assuming_it_is_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail closed: an unreadable worktree hides exactly what we look for."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, dirty_query_fails=True)
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(
        tmp_path, _TARGET, _accepting_resolver(seen)
    )

    assert resolved is False
    assert seen == []
    assert repo.continue_calls == 0


def test_an_unreadable_rebase_head_declines_before_the_resolver_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail closed when the stopped commit has no readable identity.

    An empty ``REBASE_HEAD`` costs the loop both of the things that SHA
    buys: the prompt template gates its whole rebase-mode commit context
    on ``replaying_commit_sha``, so the resolver would be asked to fix a
    commit it is never told the identity of, and ``_advanced_to_a_new_stop``
    compares SHAs, so an empty one would let any later readable SHA pass
    as proof that this stop landed. Declining hands the paused rebase to
    the caller's abort path untouched.
    """
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(loop_module, "_rev_parse_rebase_head", lambda _root: "")
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(
        tmp_path, _TARGET, _accepting_resolver(seen)
    )

    assert resolved is False
    assert seen == []
    assert repo.staged == []
    assert repo.continue_calls == 0


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


def test_completion_is_proved_against_the_pinned_replay_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The completion check must not re-read a ref a sibling can move.

    A resolution session is long, and in a shared-checkout fleet the
    mainline moves during it routinely. Verifying against the target
    NAME would report a finished replay as "not a descendant of target"
    the moment a sibling landed, discarding a good resolution in favour
    of an endpoint merge. The base recorded in ``rebase-merge/onto`` at
    the start of the loop is the only stable answer.
    """
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    verified_against: list[str] = []

    def _verify(_root: Path, upstream: str) -> bool:
        verified_against.append(upstream)
        return True

    monkeypatch.setattr(loop_module, "verify_rebase_completed_at", _verify)

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver([]))
        is True
    )
    assert verified_against == [_BASE_SHA]


def test_an_unreadable_replay_base_falls_back_to_the_target_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No ``onto`` file to read: verify exactly as before this seam existed."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(loop_module, "_rebase_base_sha", lambda _root: None)
    verified_against: list[str] = []

    def _verify(_root: Path, upstream: str) -> bool:
        verified_against.append(upstream)
        return True

    monkeypatch.setattr(loop_module, "verify_rebase_completed_at", _verify)

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver([]))
        is True
    )
    assert verified_against == [_TARGET]


def test_an_unverifiable_completion_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HEAD not descending from the replay base is not a resolved rebase."""
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


def _install_progress_git_seam(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    files: dict[str, str],
) -> None:
    """Answer ``rev-parse --git-path`` for scripted rebase state files.

    ``files`` maps the relative state path git is asked to resolve onto
    the text that file contains; anything absent resolves to a path that
    is simply not there, which is what a rebase using the OTHER backend
    looks like from here.
    """
    state_dir = root / "state"
    state_dir.mkdir(exist_ok=True)
    for relative, text in files.items():
        state_file = state_dir / relative.replace("/", "_")
        state_file.write_text(text, encoding="utf-8")

    def _fake_run_git(
        args: Sequence[str], *, cwd: Path, label: str
    ) -> GitRunResult:
        assert cwd == root
        assert label == "git-rebase-progress-path"
        relative = args[-1]
        resolved = state_dir / relative.replace("/", "_")
        return GitRunResult(
            args=tuple(args),
            returncode=0,
            stdout=f"{resolved}\n",
            stderr="",
        )

    monkeypatch.setattr(loop_module, "run_git", _fake_run_git)


def test_the_merge_backend_replay_counter_is_read_and_rendered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``rebase-merge/msgnum``+``end`` become the operator's ``commit 2/5``."""
    _install_progress_git_seam(
        monkeypatch,
        tmp_path,
        {"rebase-merge/msgnum": "2\n", "rebase-merge/end": "5\n"},
    )

    assert loop_module._read_replay_progress(tmp_path) == (2, 5)
    assert (
        status_module._phase_label(
            round_index=1,
            round_cap=3,
            stop_index=1,
            stop_cap=MAX_REBASE_CONFLICT_STOPS,
            replay_index=2,
            replay_total=5,
        )
        == "Rebase Conflict Resolution (commit 2/5, round 1/3)"
    )


def test_the_apply_backend_replay_counter_is_read_equivalently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ``rebase --apply`` replay counts in different files, same meaning."""
    _install_progress_git_seam(
        monkeypatch,
        tmp_path,
        {"rebase-apply/next": "2", "rebase-apply/last": "5"},
    )

    assert loop_module._read_replay_progress(tmp_path) == (2, 5)


def test_unreadable_or_nonsensical_replay_state_reads_as_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Absent, garbage and out-of-range counters are all just ``None``.

    The counter is cosmetic, so every one of these must degrade the
    label rather than raise into a resolution that would otherwise land.
    """
    _install_progress_git_seam(monkeypatch, tmp_path, {})
    assert loop_module._read_replay_progress(tmp_path) is None

    _install_progress_git_seam(
        monkeypatch,
        tmp_path,
        {"rebase-merge/msgnum": "two", "rebase-merge/end": "5"},
    )
    assert loop_module._read_replay_progress(tmp_path) is None

    _install_progress_git_seam(
        monkeypatch,
        tmp_path,
        {"rebase-merge/msgnum": "7", "rebase-merge/end": "5"},
    )
    assert loop_module._read_replay_progress(tmp_path) is None

    _install_progress_git_seam(
        monkeypatch,
        tmp_path,
        {"rebase-merge/msgnum": "1", "rebase-merge/end": "0"},
    )
    assert loop_module._read_replay_progress(tmp_path) is None


def test_a_failing_git_path_lookup_reads_as_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """git refusing to resolve the state path is just an unreadable counter."""

    def _failing_run_git(
        args: Sequence[str], *, cwd: Path, label: str
    ) -> GitRunResult:
        return GitRunResult(
            args=tuple(args), returncode=128, stdout="", stderr="not a git repo"
        )

    monkeypatch.setattr(loop_module, "run_git", _failing_run_git)

    assert loop_module._read_replay_progress(tmp_path) is None


def test_an_unreadable_counter_falls_back_to_the_stop_counters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No replay pair on the stop, and the label degrades to the old one."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, replay_progress=None)
    seen: list[RebaseStop] = []

    resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert seen[0].replay_index is None
    assert seen[0].replay_total is None
    assert (
        status_module._phase_label(
            round_index=1,
            round_cap=3,
            stop_index=seen[0].stop_index,
            stop_cap=seen[0].stop_cap,
            replay_index=seen[0].replay_index,
            replay_total=seen[0].replay_total,
        )
        == f"Rebase Conflict Resolution (commit 1/{MAX_REBASE_CONFLICT_STOPS}, "
        "round 1/3)"
    )


def test_the_stop_carries_the_replay_position_when_it_is_readable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The resolver is handed the real replay position, not the safety cap."""
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, replay_progress=(2, 5))
    seen: list[RebaseStop] = []

    resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert (seen[0].replay_index, seen[0].replay_total) == (2, 5)


def test_a_readable_replay_total_never_widens_the_stop_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The safety bound stays MAX_REBASE_CONFLICT_STOPS, whatever git reports.

    ``stop_cap`` feeds :func:`route_after_stop`, which terminates the
    loop. Letting the replay total reach it would replace a fixed bound
    with an accident-controlled number of agent invocations.
    """
    repo = _FakeRepo(stops=0, never_finishes=True)
    _install_seams(monkeypatch, repo, replay_progress=(1, 40))
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(
        tmp_path, _TARGET, _accepting_resolver(seen)
    )

    assert resolved is False
    assert len(seen) == MAX_REBASE_CONFLICT_STOPS
    assert {stop.stop_cap for stop in seen} == {MAX_REBASE_CONFLICT_STOPS}
