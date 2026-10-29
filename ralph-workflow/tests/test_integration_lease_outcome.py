"""Typed lease outcomes: every integration-lease condition classifies distinctly.

The 2026-10-29 symptom collapsed four unrelated conditions into one
``acquired=False`` printed as "worktree busy". These tests pin the typed
contract: only a live ``flock`` holder is contention; environment
failures are ``UNKNOWN`` with a typed reason and evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.auto_integrate_transaction import (
    LeaseOutcomeKind,
    LeaseUnknownReason,
    integration_transaction,
)


def test_missing_git_marker_is_degraded_acquired(tmp_path: Path) -> None:
    with integration_transaction(tmp_path) as outcome:
        assert outcome.acquired
        assert outcome.kind is LeaseOutcomeKind.ACQUIRED


def test_git_dir_without_head_is_unknown_git_dir_invalid(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with integration_transaction(tmp_path) as outcome:
        assert not outcome.acquired
        assert outcome.kind is LeaseOutcomeKind.UNKNOWN
        assert outcome.reason is LeaseUnknownReason.GIT_DIR_INVALID


def test_unresolvable_git_dir_is_unknown_git_dir_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "fatal: not a git repository: whatever\n"

    def _fail(*_args: object, **_kwargs: object) -> _Failed:
        return _Failed()

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_transaction.run_git", _fail
    )
    with integration_transaction(tmp_path) as outcome:
        assert outcome.kind is LeaseOutcomeKind.UNKNOWN
        assert outcome.reason is LeaseUnknownReason.GIT_DIR_UNRESOLVED
        assert "not a git repository" in (outcome.evidence or "")


def test_lock_open_io_error_is_unknown_lock_io_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_real_git_marker(tmp_path)

    class _Ok:
        returncode = 0
        stdout = str(tmp_path / ".git") + "\n"
        stderr = ""

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_transaction.run_git",
        lambda *_a, **_k: _Ok(),
    )

    real_open = Path.open

    def _raise(self: Path, *args: object, **kwargs: object) -> object:
        if self.name == "ralph-auto-integrate.lock":
            raise OSError(13, "Permission denied")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", _raise)
    with integration_transaction(tmp_path) as outcome:
        assert outcome.kind is LeaseOutcomeKind.UNKNOWN
        assert outcome.reason is LeaseUnknownReason.LOCK_IO_ERROR
        assert outcome.evidence == "13"


def test_live_holder_is_contention_and_dead_holder_releases(tmp_git_repo: Path) -> None:
    from ralph.pipeline.auto_integrate_catchup_coordination import (
        _try_lock,
        _unlock,
    )

    lock_path = tmp_git_repo / ".git" / "ralph-auto-integrate.lock"
    with lock_path.open("a+", encoding="utf-8") as holder:
        assert _try_lock(holder)
        with integration_transaction(tmp_git_repo) as outcome:
            assert not outcome.acquired
            assert outcome.kind is LeaseOutcomeKind.HELD_BY_LIVE_OWNER
            assert "busy" in outcome.describe("worktree busy; retrying")
        _unlock(holder)
    # A dead (released) holder self-releases: the next attempt acquires,
    # and the lock file itself is never removed.
    with integration_transaction(tmp_git_repo) as outcome:
        assert outcome.acquired
    assert lock_path.exists()


def test_busy_wording_is_reserved_for_live_contention(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with integration_transaction(tmp_path) as outcome:
        line = outcome.describe("worktree busy; retrying")
        assert "worktree busy" not in line
        assert "GIT_DIR_INVALID" in line


def _make_real_git_marker(root: Path) -> None:
    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
