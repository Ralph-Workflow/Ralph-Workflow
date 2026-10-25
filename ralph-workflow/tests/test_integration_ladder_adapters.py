"""Production git-adapter argv contract, captured at the ``run_git`` seam.

These tests patch the queue-routed runner the production adapter resolves
(no subprocess is ever spawned) and assert the exact argv tuples: config
flags before the subcommand, the compare-and-swap ``update-ref`` old-value
form, and the abort mapping for every :class:`InProgressKind` the ladder
can meet. The plumbing under test comes from the public
:func:`~ralph.pipeline.integration_ladder.default_git_plumbing` constructor.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.git_run_result import GitRunResult
from ralph.pipeline.integration_ladder import GitPlumbing, default_git_plumbing
from ralph.pipeline.integration_observation import InProgressKind

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

pytestmark = pytest.mark.timeout_seconds(30)


def _install_run_git_recorder(
    monkeypatch: pytest.MonkeyPatch,
    script: Callable[[Sequence[str], Path | None, str], GitRunResult],
) -> list[tuple[str, ...]]:
    """Patch the queue-routed runner with a recorder; returns the argv list.

    The production adapter imports ``run_git`` lazily from
    :mod:`ralph.git.subprocess_runner`, so patching the module attribute is
    the exact seam the adapter resolves — no subprocess is ever spawned.
    """

    from ralph.git import subprocess_runner

    argv: list[tuple[str, ...]] = []

    def _fake_run_git(args: Sequence[str], *, cwd: Path | None, label: str) -> GitRunResult:
        argv.append(tuple(args))
        return script(args, cwd, label)

    monkeypatch.setattr(subprocess_runner, "run_git", _fake_run_git)
    return argv


def _ok_script(args: Sequence[str], cwd: Path | None, label: str) -> GitRunResult:
    return GitRunResult(args=("git", *args), returncode=0, stdout="", stderr="")


def test_adapter_changed_paths_puts_config_flags_before_the_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``git status ... -c key=val`` exits 129; the config flags must lead."""
    argv = _install_run_git_recorder(monkeypatch, _ok_script)
    plumbing = default_git_plumbing()

    assert plumbing.changed_paths(Path("/nonexistent-root")) == ()
    (status_argv,) = argv
    assert status_argv[:2] == ("-c", "status.renames=false")
    assert status_argv[2] == "status"


def test_adapter_changed_paths_probe_error_is_unknown_never_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exit-129 shape from the ordering bug must read as ``None``."""

    def _script(args: Sequence[str], cwd: Path | None, label: str) -> GitRunResult:
        return GitRunResult(
            args=("git", *args), returncode=129, stdout="", stderr="error: unknown switch 'c'"
        )

    _install_run_git_recorder(monkeypatch, _script)
    plumbing = default_git_plumbing()

    assert plumbing.changed_paths(Path("/nonexistent-root")) is None


def test_adapter_land_uses_old_value_update_ref_and_races_issue_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CAS argv carries ``--no-deref`` and the old value; a race never updates."""
    expected = "a" * 40
    tip = "b" * 40
    state = {"current": expected}

    def _script(args: Sequence[str], cwd: Path | None, label: str) -> GitRunResult:
        full = ("git", *args)
        if tuple(args) == ("rev-parse", "HEAD"):
            return GitRunResult(args=full, returncode=0, stdout=f"{tip}\n", stderr="")
        if tuple(args) == ("rev-parse", "--verify", "refs/heads/main"):
            return GitRunResult(args=full, returncode=0, stdout=f"{state['current']}\n", stderr="")
        if args[:1] == ("update-ref",):
            state["current"] = tip
            return GitRunResult(args=full, returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected argv: {args}")

    argv = _install_run_git_recorder(monkeypatch, _script)
    plumbing = default_git_plumbing()

    assert (
        plumbing.land_in_worktree(Path("/root"), Path("/wt"), "main", expected_sha=expected)
        == "landed"
    )
    assert ("update-ref", "--no-deref", "refs/heads/main", tip, expected) in argv

    # The tip already at the expectation is a no-op: no second update-ref.
    assert (
        plumbing.land_in_worktree(Path("/root"), Path("/wt"), "main", expected_sha=tip)
        == "landed-already"
    )

    # A concurrent advance: the expectation is stale, NO update-ref is issued.
    state["current"] = "c" * 40
    update_refs = [a for a in argv if a[:1] == ("update-ref",)]
    assert (
        plumbing.land_in_worktree(Path("/root"), Path("/wt"), "main", expected_sha=expected)
        == "target-moved"
    )
    assert [a for a in argv if a[:1] == ("update-ref",)] == update_refs


def test_adapter_repoint_not_now_outcomes_never_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A gone worktree or a missing observation is a typed not-now, never a reset."""

    def _script(args: Sequence[str], cwd: Path | None, label: str) -> GitRunResult:
        return GitRunResult(args=("git", *args), returncode=0, stdout="b" * 40 + "\n", stderr="")

    argv = _install_run_git_recorder(monkeypatch, _script)
    plumbing = default_git_plumbing()
    gone = tmp_path / "gone"
    gone.mkdir()

    def _reset_args() -> list[tuple[str, ...]]:
        return [a for a in argv if a[2:3] == ("reset",)]

    assert plumbing.repoint_original(gone, tmp_path, observed_sha="a" * 40) == "worktree-missing"
    assert _reset_args() == []
    assert plumbing.repoint_original(gone, tmp_path, observed_sha=None) == "head-unreadable"
    assert _reset_args() == []


def test_adapter_abort_operation_covers_every_in_progress_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every abortable kind maps to its git abort verb with hooks disabled."""
    argv = _install_run_git_recorder(monkeypatch, _ok_script)
    plumbing: GitPlumbing = default_git_plumbing()

    expected_commands = {
        InProgressKind.REBASE: ("rebase", "--abort"),
        InProgressKind.MERGE: ("merge", "--abort"),
        InProgressKind.CHERRY_PICK: ("cherry-pick", "--abort"),
        InProgressKind.REVERT: ("revert", "--abort"),
        InProgressKind.AM: ("am", "--abort"),
        # A multi-step sequencer owns state files, not one operation:
        # ``--quit`` forgets the sequence without committing.
        InProgressKind.SEQUENCER: ("cherry-pick", "--quit"),
    }
    for kind, command in expected_commands.items():
        assert plumbing.abort_operation(Path("/root"), kind) is True
        assert ("-c", "core.hooksPath=/dev/null", *command) in argv
