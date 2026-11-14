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
from ralph.pipeline.conflict_resolution import (
    deterministic_resolution as deterministic_module,
)
from ralph.pipeline.conflict_resolution import rebase_loop as loop_module
from ralph.pipeline.conflict_resolution import status as status_module
from ralph.pipeline.conflict_resolution.graph import (
    PHASE_RESOLUTION,
    TERMINAL_ABANDONED,
    TERMINAL_RESOLVED,
    route_after_stop,
)
from ralph.pipeline.conflict_resolution.progress import (
    RebaseResolutionProgress,
    load_progress,
    save_progress,
)
from ralph.pipeline.conflict_resolution.rebase_loop import (
    RebaseStop,
    resolve_rebase_in_progress,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession

if TYPE_CHECKING:
    import pytest

_TARGET = "main"
_CONFLICTED = ["src/alpha.py"]
_BASE_SHA = "ba5e0000000000000000000000000000000000ba"
_ORIG_HEAD_SHA = "0219000000000000000000000000000000000fea"
_STOP_CAP = 10


class _FakeRepo:
    def __init__(self, stops: int, *, never_finishes: bool = False) -> None:
        self.remaining = stops
        self.never_finishes = never_finishes
        self.continue_calls = 0
        self.staged: list[list[str]] = []

    def in_progress(self, _root: Path) -> bool:
        return self.never_finishes or self.remaining > 0

    def continue_rebase(self, _root: Path, *, skip_empty: bool = True) -> None:
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
    _install_worktree_seam(
        monkeypatch,
        dirty_after_resolution=dirty_after_resolution,
        dirty_query_fails=dirty_query_fails,
    )
    monkeypatch.setattr(loop_module, "rebase_in_progress_at", repo.in_progress)
    monkeypatch.setattr(loop_module, "staged_conflict_marker_paths", lambda _root: [])
    if repo.never_finishes:
        monkeypatch.setattr(loop_module, "record_landed_stop", lambda _root, _stop: None)
    monkeypatch.setattr(loop_module, "get_conflicted_files", lambda **_kwargs: list(_CONFLICTED))
    monkeypatch.setattr(loop_module, "_rev_parse_rebase_head", lambda _root: "abc1234")
    monkeypatch.setattr(loop_module, "_rebase_head_subject", lambda _root: "feature edit")
    monkeypatch.setattr(deterministic_module, "conflict_stage_entries", lambda _root, _paths: {})

    def _stage(_root: Path, paths: Sequence[str]) -> bool:
        repo.staged.append(list(paths))
        return stage_ok

    monkeypatch.setattr(loop_module, "stage_paths", _stage)
    monkeypatch.setattr(loop_module, "paths_with_conflict_markers", lambda _r, _p: list(surviving))
    monkeypatch.setattr(loop_module, "unmerged_paths", lambda _r: list(unmerged))
    monkeypatch.setattr(loop_module, "continue_rebase_at", repo.continue_rebase)
    monkeypatch.setattr(loop_module, "_rebase_base_sha", lambda _root: _BASE_SHA)
    monkeypatch.setattr(loop_module, "_rebase_orig_head_sha", lambda _root: _ORIG_HEAD_SHA)
    monkeypatch.setattr(loop_module, "_read_replay_progress", lambda _root: replay_progress)
    monkeypatch.setattr(loop_module, "verify_rebase_completed_at", lambda _r, _t: verified)
    monkeypatch.setattr(
        loop_module,
        "current_rebase_identity",
        lambda _root: (_ORIG_HEAD_SHA, _BASE_SHA),
    )


def _install_worktree_seam(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dirty_after_resolution: Sequence[str] | None,
    dirty_query_fails: bool,
) -> None:
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
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is True
    assert repo.continue_calls == 1


def test_resolve_rebase_in_progress_lands_after_empty_commit_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    empty_stop_handled = {"value": False}

    def _continue_after_empty_stop(root: Path, *, skip_empty: bool = True) -> None:
        assert skip_empty
        if not empty_stop_handled["value"]:
            empty_stop_handled["value"] = True
        repo.continue_rebase(root)

    monkeypatch.setattr(loop_module, "continue_rebase_at", _continue_after_empty_stop)
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is True
    assert empty_stop_handled["value"] is True
    assert repo.continue_calls == 1


def test_rebase_empty_stop_continuation_failure_abandons(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)

    def _old_continue(root: Path, *, skip_empty: bool = True) -> None:
        raise RebaseContinuationError("previous cherry-pick is empty")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _old_continue)

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver([])) is False
    assert repo.continue_calls == 0


def test_three_stops_drive_three_resolver_calls_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=3)
    _install_seams(monkeypatch, repo)
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is True
    assert [stop.stop_index for stop in seen] == [1, 2, 3]


def test_declining_resolver_never_continues_the_rebase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, lambda _root, _target, _stop: False)

    assert resolved is False
    assert repo.continue_calls == 0


def test_surviving_conflict_markers_reject_a_claimed_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, surviving=["src/alpha.py"])
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert resolved is False
    assert repo.continue_calls == 0


def test_residual_unmerged_paths_reject_a_claimed_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, unmerged=["src/alpha.py"])
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert resolved is False


def test_failure_to_stage_rejects_the_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, stage_ok=False)
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert resolved is False


def test_a_resolver_that_edits_an_unrequested_path_keeps_the_in_scope_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(
        monkeypatch,
        repo,
        dirty_after_resolution=[*_CONFLICTED, "docs/unrelated.md"],
    )
    reverted: list[tuple[str, ...]] = []

    def _revert(_root: Path, paths: tuple[str, ...]) -> bool:
        reverted.append(paths)
        return True

    monkeypatch.setattr(loop_module, "_revert_unrequested_paths", _revert)
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert resolved is True
    assert reverted == [("docs/unrelated.md",)]
    assert repo.staged == [list(_CONFLICTED)]
    assert repo.continue_calls == 1


def test_a_worktree_dirty_before_the_resolver_ran_is_not_blamed_on_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(
        loop_module,
        "_worktree_dirty_paths",
        lambda _root: frozenset([*_CONFLICTED, "docs/already-dirty.md"]),
    )
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is True


def test_an_unreadable_worktree_declines_rather_than_assuming_it_is_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, dirty_query_fails=True)
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert resolved is False
    assert seen == []
    assert repo.continue_calls == 0


def test_an_unreadable_rebase_head_declines_before_the_resolver_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(loop_module, "_rev_parse_rebase_head", lambda _root: "")
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert resolved is False
    assert seen == []
    assert repo.staged == []
    assert repo.continue_calls == 0


def test_conflict_remaining_error_from_continue_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)

    def _raise(root: Path, *, skip_empty: bool = True) -> None:
        raise ConflictRemainingError("conflicts still exist in the index")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _raise)
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is False


def test_continuation_error_from_continue_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)

    def _raise(root: Path, *, skip_empty: bool = True) -> None:
        raise RebaseContinuationError("failed to continue rebase")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _raise)
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is False


def test_a_continue_that_lands_but_hits_the_next_conflict_is_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=2)
    _install_seams(monkeypatch, repo)
    heads = iter(["sha-one", "sha-two"])
    monkeypatch.setattr(loop_module, "_rev_parse_rebase_head", lambda _root: next(heads, "sha-two"))

    def _continue_then_conflict(root: Path, *, skip_empty: bool = True) -> None:
        repo.continue_rebase(root)
        if repo.remaining > 0:
            raise RebaseContinuationError("could not apply the next commit")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _continue_then_conflict)
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is True


def test_a_continue_that_fails_on_the_same_stop_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1, never_finishes=True)
    _install_seams(monkeypatch, repo)

    def _raise(root: Path, *, skip_empty: bool = True) -> None:
        raise RebaseContinuationError("hook rejected the commit")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _raise)
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is False
    assert len(seen) == 1


def test_rebase_finishing_during_continue_is_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    calls: list[int] = []

    def _finish(_root: Path, *, skip_empty: bool = True) -> None:
        calls.append(1)
        repo.remaining = 0
        raise NoRebaseInProgressError("no rebase in progress")

    monkeypatch.setattr(loop_module, "continue_rebase_at", _finish)
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is True


def test_a_rebase_that_never_finishes_exhausts_the_stop_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=0, never_finishes=True)
    _install_seams(monkeypatch, repo)
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert resolved is False
    assert len(seen) == _STOP_CAP


def test_configured_stop_cap_applies_to_every_rebase_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=0, never_finishes=True)
    _install_seams(monkeypatch, repo)
    seen: list[RebaseStop] = []

    assert (
        resolve_rebase_in_progress(
            tmp_path,
            _TARGET,
            _accepting_resolver(seen),
            session=ResolutionSession(max_rebase_conflict_stops=2),
        )
        is False
    )
    assert [stop.stop_cap for stop in seen] == [2, 2]


def test_a_paused_rebase_with_its_stop_already_staged_is_continued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(loop_module, "get_conflicted_files", lambda **_kwargs: [])
    monkeypatch.setattr(loop_module, "unmerged_paths", lambda _root: [])
    monkeypatch.setattr(loop_module, "_staged_paths", lambda _root: ["f.txt"])
    monkeypatch.setattr(loop_module, "paths_with_conflict_markers", lambda _root, _paths: [])
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is True
    assert seen == [], "no resolver is needed for a stop that is already staged"


def test_a_paused_rebase_that_is_still_conflicted_is_not_continued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(loop_module, "get_conflicted_files", lambda **_kwargs: [])
    monkeypatch.setattr(loop_module, "unmerged_paths", lambda _root: ["still.py"])
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is False


def test_an_unexpected_exception_declines_without_propagating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)

    def _explode(**_kwargs: object) -> list[str]:
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(loop_module, "get_conflicted_files", _explode)

    assert (
        resolve_rebase_in_progress(tmp_path, _TARGET, lambda _root, _target, _stop: True) is False
    )


def test_completion_is_proved_against_the_pinned_replay_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    verified_against: list[str] = []

    def _verify(_root: Path, upstream: str) -> bool:
        verified_against.append(upstream)
        return True

    monkeypatch.setattr(loop_module, "verify_rebase_completed_at", _verify)

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver([])) is True
    assert verified_against == [_BASE_SHA]


def test_an_unreadable_replay_base_falls_back_to_the_target_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(loop_module, "_rebase_base_sha", lambda _root: None)
    verified_against: list[str] = []

    def _verify(_root: Path, upstream: str) -> bool:
        verified_against.append(upstream)
        return True

    monkeypatch.setattr(loop_module, "verify_rebase_completed_at", _verify)

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver([])) is True
    assert verified_against == [_TARGET]


def test_an_unverifiable_completion_declines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, verified=False)
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is False


def test_the_stop_carries_the_replayed_commit_and_the_conflicted_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
            stop_cap=_STOP_CAP,
        )
    ]


def test_route_after_stop_resolves_continues_and_abandons() -> None:
    assert route_after_stop(1, True, _STOP_CAP) == TERMINAL_RESOLVED
    assert route_after_stop(1, False, _STOP_CAP) == PHASE_RESOLUTION
    assert route_after_stop(_STOP_CAP, False, _STOP_CAP) == TERMINAL_ABANDONED


def _install_progress_git_seam(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    files: dict[str, str],
) -> None:
    state_dir = root / "state"
    scripted_files = {
        state_dir / relative.replace("/", "_"): text for relative, text in files.items()
    }

    def _fake_run_git(args: Sequence[str], *, cwd: Path, label: str) -> GitRunResult:
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

    def _fake_read_text(path: Path) -> str:
        try:
            return scripted_files[path]
        except KeyError as exc:
            raise OSError("scripted state file is absent") from exc

    monkeypatch.setattr(loop_module, "run_git", _fake_run_git)
    monkeypatch.setattr(loop_module, "_read_rebase_state_text", _fake_read_text)


def test_the_merge_backend_replay_counter_is_read_and_rendered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
            stop_cap=_STOP_CAP,
            replay_index=2,
            replay_total=5,
        )
        == "Rebase Conflict Resolution (commit 2/5, round 1/3)"
    )


def test_the_apply_backend_replay_counter_is_read_equivalently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_progress_git_seam(
        monkeypatch,
        tmp_path,
        {"rebase-apply/next": "2", "rebase-apply/last": "5"},
    )

    assert loop_module._read_replay_progress(tmp_path) == (2, 5)


def test_unreadable_or_nonsensical_replay_state_reads_as_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    def _failing_run_git(args: Sequence[str], *, cwd: Path, label: str) -> GitRunResult:
        return GitRunResult(args=tuple(args), returncode=128, stdout="", stderr="not a git repo")

    monkeypatch.setattr(loop_module, "run_git", _failing_run_git)

    assert loop_module._read_replay_progress(tmp_path) is None


def test_an_unreadable_counter_falls_back_to_the_stop_counters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
        == f"Rebase Conflict Resolution (commit 1/{_STOP_CAP}, "
        "round 1/3)"
    )


def test_the_stop_carries_the_replay_position_when_it_is_readable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo, replay_progress=(2, 5))
    seen: list[RebaseStop] = []

    resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert (seen[0].replay_index, seen[0].replay_total) == (2, 5)


def test_a_readable_replay_total_never_widens_the_stop_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=0, never_finishes=True)
    _install_seams(monkeypatch, repo, replay_progress=(1, 40))
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert resolved is False
    assert len(seen) == _STOP_CAP
    assert {stop.stop_cap for stop in seen} == {_STOP_CAP}


def test_mode_only_conflict_resolved_without_invoking_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(
        deterministic_module,
        "conflict_stage_entries",
        lambda _root, _paths: {
            "src/alpha.py": {
                1: ("100644", "blob"),
                2: ("100644", "blob"),
                3: ("100755", "blob"),
            }
        },
    )
    monkeypatch.setattr(
        loop_module,
        "run_git",
        lambda *_args, **_kwargs: GitRunResult(args=(), returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        deterministic_module,
        "run_git",
        lambda *_args, **_kwargs: GitRunResult(args=(), returncode=0, stdout="", stderr=""),
    )
    called: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(called)) is True
    assert called == []
    assert repo.staged == [_CONFLICTED]


def test_gitlink_conflict_resolved_via_ancestor_pick(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(
        deterministic_module,
        "conflict_stage_entries",
        lambda _root, _paths: {"src/alpha.py": {2: ("160000", "old"), 3: ("160000", "new")}},
    )
    calls: list[tuple[str, ...]] = []

    def _run(args: tuple[str, ...], **_kwargs: object) -> GitRunResult:
        calls.append(args)
        return GitRunResult(args=(), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(loop_module, "run_git", _run)
    monkeypatch.setattr(deterministic_module, "run_git", _run)
    called: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(called)) is True
    assert called == []
    assert ("update-index", "--cacheinfo", "160000,new,src/alpha.py") in calls


def test_gitlink_conflict_diverged_declines_to_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(
        deterministic_module,
        "conflict_stage_entries",
        lambda _root, _paths: {"src/alpha.py": {2: ("160000", "left"), 3: ("160000", "right")}},
    )

    def _run(args: tuple[str, ...], **_kwargs: object) -> GitRunResult:
        rc = 1 if "merge-base" in args else 0
        return GitRunResult(args=(), returncode=rc, stdout="", stderr="")

    monkeypatch.setattr(loop_module, "run_git", _run)
    monkeypatch.setattr(deterministic_module, "run_git", _run)
    called: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(called)) is True
    assert len(called) == 1


def test_mixed_deterministic_conflict_declines_whole_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(loop_module, "get_conflicted_files", lambda **_kwargs: ["one", "two"])
    monkeypatch.setattr(
        deterministic_module,
        "conflict_stage_entries",
        lambda _root, _paths: {
            "one": {2: ("160000", "left"), 3: ("160000", "right")},
            "two": {2: ("100644", "blob"), 3: ("100755", "blob")},
        },
    )
    called: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(called)) is True
    assert called[0].conflicted_files == ("one", "two")


def test_resolve_skips_sidecar_landed_shas_and_starts_at_the_first_unlanded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    save_progress(
        tmp_path,
        RebaseResolutionProgress(
            landed_shas=["aaa1"],
            remaining_paths=list(_CONFLICTED),
            feature_sha=_ORIG_HEAD_SHA,
            target_sha=_BASE_SHA,
        ),
    )
    repo = _FakeRepo(stops=2)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(
        loop_module,
        "_rev_parse_rebase_head",
        lambda _root: "aaa1" if repo.remaining == 2 else "bbb2",
    )
    seen: list[RebaseStop] = []

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen))

    assert resolved is True
    assert [stop.sha for stop in seen] == ["bbb2"]
    assert repo.continue_calls == 2


def test_four_landed_stops_survive_a_fifth_stop_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shas = ("s1", "s2", "s3", "s4", "s5")
    repo = _FakeRepo(stops=5)
    _install_seams(monkeypatch, repo)

    def _head(_root: Path) -> str:
        landed = 5 - repo.remaining
        return shas[landed]

    monkeypatch.setattr(loop_module, "_rev_parse_rebase_head", _head)
    seen: list[RebaseStop] = []

    def _resolve(_root: Path, _target: str, stop: RebaseStop) -> bool:
        seen.append(stop)
        return stop.sha != "s5"

    resolved = resolve_rebase_in_progress(tmp_path, _TARGET, _resolve)

    assert resolved is False
    assert [stop.sha for stop in seen] == list(shas)
    progress = load_progress(tmp_path)
    assert progress is not None
    assert progress.landed_shas == ["s1", "s2", "s3", "s4"]
    assert repo.continue_calls == 4


def test_a_staged_stop_with_surviving_markers_is_not_continued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _FakeRepo(stops=1)
    _install_seams(monkeypatch, repo)
    monkeypatch.setattr(loop_module, "get_conflicted_files", lambda **_kwargs: [])
    monkeypatch.setattr(loop_module, "unmerged_paths", lambda _root: [])
    monkeypatch.setattr(loop_module, "_staged_paths", lambda _root: ["f.txt"])
    monkeypatch.setattr(loop_module, "paths_with_conflict_markers", lambda _root, _paths: ["f.txt"])
    seen: list[RebaseStop] = []

    assert resolve_rebase_in_progress(tmp_path, _TARGET, _accepting_resolver(seen)) is False
    assert seen == []
