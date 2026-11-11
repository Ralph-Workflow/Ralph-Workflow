from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.git_run_result import GitRunResult
from ralph.pipeline._auto_integrate_recovery_git_state import detached_branch_name

if TYPE_CHECKING:
    import pytest


def _reflog(*lines: str) -> GitRunResult:
    return GitRunResult(
        args=("git", "reflog", "--format=%H%x00%gs", "-n", "50"),
        returncode=0,
        stdout="\n".join(lines) + "\n",
        stderr="",
    )


def _ok(stdout: str = "") -> GitRunResult:
    return GitRunResult(args=("git",), returncode=0, stdout=stdout, stderr="")


def _err(returncode: int = 1) -> GitRunResult:
    return GitRunResult(args=("git",), returncode=returncode, stdout="", stderr="")


def _stub(
    reflog_result: GitRunResult,
    *,
    show_ref: GitRunResult | None = None,
    rev_parse_branch: GitRunResult | None = None,
    rev_parse_head: GitRunResult | None = None,
):

    def _run_git(
        args: tuple[str, ...] | list[str],
        cwd: Path | None = None,
        label: str = "",
    ) -> GitRunResult:
        a = tuple(args)
        if a and a[0] == "reflog":
            return reflog_result
        if a and a[0] == "show-ref":
            return show_ref if show_ref is not None else _err()
        if a and a[0] == "rev-parse" and "HEAD" in a and "refs/heads" not in " ".join(a):
            return rev_parse_head if rev_parse_head is not None else _err()
        if a and a[0] == "rev-parse" and "refs/heads" in " ".join(a):
            return rev_parse_branch if rev_parse_branch is not None else _err()
        return _err()

    return _run_git


class TestDetachedBranchNameFailClosed:
    def test_malformed_empty_destination_then_valid_old_branch_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = (
            f"{sha}\x00checkout: moving from main to \n"
            f"{sha}\x00checkout: moving from main to feature\n"
        )
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("abc"),
                rev_parse_head=_ok("abc"),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_malformed_missing_to_separator_then_valid_old_branch_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = (
            f"{sha}\x00checkout: moving from main\n{sha}\x00checkout: moving from main to feature\n"
        )
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("abc"),
                rev_parse_head=_ok("abc"),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_malformed_whitespace_destination_then_valid_old_branch_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = (
            f"{sha}\x00checkout: moving from main to    \n"
            f"{sha}\x00checkout: moving from main to feature\n"
        )
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("abc"),
                rev_parse_head=_ok("abc"),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_malformed_switch_branch_then_valid_old_branch_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = (
            f"{sha}\x00switch branch to \n{sha}\x00checkout: moving from main to feature\n"
        )
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("abc"),
                rev_parse_head=_ok("abc"),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_irrelevant_entries_before_checkout_are_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = (
            f"{sha}\x00commit: add some work\n"
            f"{sha}\x00merge: merge feature into main\n"
            f"{sha}\x00cherry-pick: pick a commit\n"
            f"{sha}\x00checkout: moving from main to feature\n"
        )
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("abc"),
                rev_parse_head=_ok("abc"),
            ),
        )
        assert detached_branch_name(tmp_path) == "feature"

    def test_empty_branch_tip_and_empty_head_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok(""),
                rev_parse_head=_ok(""),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_empty_branch_tip_only_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok(""),
                rev_parse_head=_ok(sha),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_empty_head_only_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok(sha),
                rev_parse_head=_ok(""),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_whitespace_only_branch_tip_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_ok("   \n"),
                rev_parse_head=_ok("   \n"),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_exact_tip_match_returns_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok(sha),
                rev_parse_branch=_ok(sha),
                rev_parse_head=_ok(sha),
            ),
        )
        assert detached_branch_name(tmp_path) == "feature"

    def test_sha_destination_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        sha_dest = "b" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to {sha_dest}\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_err(),
                rev_parse_branch=_ok(sha),
                rev_parse_head=_ok(sha),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_mismatched_tip_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        sha_other = "b" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok(sha),
                rev_parse_branch=_ok(sha),
                rev_parse_head=_ok(sha_other),
            ),
        )
        assert detached_branch_name(tmp_path) is None

    def test_rev_parse_failure_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sha = "a" * 40
        reflog_text = f"{sha}\x00checkout: moving from main to feature\n"
        monkeypatch.setattr(
            "ralph.pipeline._auto_integrate_recovery_git_state.run_git",
            _stub(
                _reflog(*reflog_text.splitlines()),
                show_ref=_ok("abc"),
                rev_parse_branch=_err(128),
                rev_parse_head=_ok(sha),
            ),
        )
        assert detached_branch_name(tmp_path) is None
