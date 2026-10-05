"""Merge obstructions must never silently strand a branch behind its target.

Observed 2026-10-05: a worktree held 22 untracked files that were
byte-identical to files ``main`` tracks (leftovers of an earlier
integration). ``git merge main`` refused to start (exit 2, "untracked
working tree files would be overwritten by merge"); Ralph recorded a
generic "endpoint merge conflicted" with no resolver able to act, saw a
clean tree, and began planning 40 commits behind ``main``.

Git is injected through ``run_git`` in ``ralph.git.merge`` and
``ralph.git.merge_obstructions`` so this file stays in
the default, budget-tracked suite; only ``tmp_path`` files are touched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.git_run_result import GitRunResult
from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import MergeResult, merge_target_into_current
from ralph.git.merge_obstructions import ancestry_state, clear_untracked_merge_obstructions
from ralph.pipeline.auto_integrate_outcome import classify_merge_only_outcome

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    import pytest

    from ralph.git.subprocess_runner import GitRunOptions

_SAME_OID = "a" * 40
_TARGET_OID_FOR_EDITED = "b" * 40
_LOCAL_OID_FOR_EDITED = "c" * 40

_UNTRACKED_REFUSAL = (
    "error: The following untracked working tree files would be overwritten by merge:\n"
    "\tdocs/edited.md\n"
    "Please move or remove them before you merge.\n"
    "Aborting\n"
)


def _stub_git(
    monkeypatch: pytest.MonkeyPatch,
    respond: Callable[[tuple[str, ...]], tuple[int, str, str]],
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def _fake_run_git(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        label: str = "",
        options: GitRunOptions | None = None,
    ) -> GitRunResult:
        del cwd, label, options
        raw = tuple(args)
        argv = (
            raw[len(COMMIT_PIN_CONFIG_ARGS) :]
            if raw[: len(COMMIT_PIN_CONFIG_ARGS)] == COMMIT_PIN_CONFIG_ARGS
            else raw
        )
        calls.append(argv)
        returncode, stdout, stderr = respond(argv)
        return GitRunResult(args=raw, returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr("ralph.git.merge.run_git", _fake_run_git)
    monkeypatch.setattr("ralph.git.merge_obstructions.run_git", _fake_run_git)
    return calls


def _obstruction_repo(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "same.md").write_text("same\n", encoding="utf-8")
    (tmp_path / "docs" / "edited.md").write_text("local work\n", encoding="utf-8")
    (tmp_path / "scratch.txt").write_text("not in target\n", encoding="utf-8")


def _obstruction_git(argv: tuple[str, ...]) -> tuple[int, str, str]:
    if argv[:2] == ("ls-files", "--others"):
        return 0, "docs/same.md\0docs/edited.md\0scratch.txt\0", ""
    if argv[:2] == ("ls-tree", "-r"):
        return (
            0,
            f"100644 blob {_SAME_OID}\tdocs/same.md\0"
            f"100644 blob {_TARGET_OID_FOR_EDITED}\tdocs/edited.md\0",
            "",
        )
    if argv[0] == "hash-object":
        oids = {"docs/edited.md": _LOCAL_OID_FOR_EDITED, "docs/same.md": _SAME_OID}
        return 0, "".join(f"{oids[path]}\n" for path in argv[2:]), ""
    return 0, "", ""


def test_identical_untracked_obstruction_is_removed_and_local_work_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _obstruction_repo(tmp_path)
    _stub_git(monkeypatch, _obstruction_git)

    removed = clear_untracked_merge_obstructions(tmp_path, "main")

    assert removed == ("docs/same.md",)
    assert not (tmp_path / "docs" / "same.md").exists()
    assert (tmp_path / "docs" / "edited.md").read_text(encoding="utf-8") == "local work\n"
    assert (tmp_path / "scratch.txt").exists()


def test_unreadable_target_tree_removes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _obstruction_repo(tmp_path)

    def _respond(argv: tuple[str, ...]) -> tuple[int, str, str]:
        if argv[:2] == ("ls-tree", "-r"):
            return 128, "", "fatal: not a valid object name main"
        return _obstruction_git(argv)

    _stub_git(monkeypatch, _respond)

    assert clear_untracked_merge_obstructions(tmp_path, "main") == ()
    assert (tmp_path / "docs" / "same.md").exists()


def test_merge_clears_identical_obstructions_before_merging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _obstruction_repo(tmp_path)
    calls = _stub_git(monkeypatch, _obstruction_git)

    assert merge_target_into_current(tmp_path, "main") == MergeResult(outcome="success")
    merge_index = calls.index(("merge", "--no-edit", "--", "main"))
    assert any(call[0] == "hash-object" for call in calls[:merge_index])
    assert not (tmp_path / "docs" / "same.md").exists()


def test_refused_merge_names_the_obstruction_instead_of_a_generic_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _obstruction_repo(tmp_path)

    def _respond(argv: tuple[str, ...]) -> tuple[int, str, str]:
        if argv[0] == "merge" and argv[1] == "--no-edit":
            return 2, "", _UNTRACKED_REFUSAL
        return _obstruction_git(argv)

    _stub_git(monkeypatch, _respond)

    result = merge_target_into_current(tmp_path, "main")

    assert result.outcome == "conflict"
    assert result.reason == ("merge refused: untracked files would be overwritten: docs/edited.md")
    assert result.blocked_paths == ("docs/edited.md",)
    _action, headline = classify_merge_only_outcome(result)
    assert headline == (
        "endpoint merge refused: untracked files would be overwritten: docs/edited.md"
    )


def test_ancestry_state_separates_no_from_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    codes = iter((0, 1, 128))
    _stub_git(monkeypatch, lambda _argv: (next(codes), "", ""))

    assert ancestry_state("/repo", "main", "HEAD") is True
    assert ancestry_state("/repo", "main", "HEAD") is False
    assert ancestry_state("/repo", "main", "HEAD") is None
