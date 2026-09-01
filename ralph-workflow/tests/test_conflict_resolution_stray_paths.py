"""Tests for the guard that keeps a resolver inside the paths it was given.

The conflict prompt forbids editing any path that is not conflicted.
:mod:`ralph.pipeline.conflict_resolution.stray_paths` is what makes that
prohibition real, and the three behaviours pinned here are the ones
whose earlier versions each threw away a resolution that had actually
succeeded: charging Ralph's own ``.agent/`` writes to the agent,
destroying an untracked file instead of setting it aside, and rejecting
a stop because an untracked directory appeared.

The per-stop gate that calls these primitives lives in
``tests/test_conflict_resolution_rebase_loop.py`` with the rest of the
loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.git_run_result import GitRunResult
from ralph.pipeline.conflict_resolution.stray_paths import (
    is_ralph_workspace_path,
    move_stray_aside,
    restore_one_unrequested_path,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_ralphs_own_agent_directory_is_not_charged_to_the_resolver() -> None:
    """The prompt is rendered INSIDE the worktree, under `.agent/tmp/`.

    Anything Ralph writes there during a resolution showed up as a stray
    edit by the agent, and the stop was rejected -- abandoning a rebase
    the resolver had actually resolved.
    """
    assert is_ralph_workspace_path(".agent/tmp/rebase_conflict_resolution_prompt.md") is True
    assert is_ralph_workspace_path(".agent") is True
    assert is_ralph_workspace_path("src/agent_config.py") is False
    assert is_ralph_workspace_path("docs/.agentic.md") is False


def test_a_stray_file_is_moved_aside_not_destroyed(tmp_path: Path) -> None:
    """Strays are INFERRED to be the resolver's, so destroying them is a guess.

    Anything that appears during the session looks the same -- including
    an operator's own file in a shared checkout -- and `unlink()` made
    that guess unrecoverable.
    """
    stray = tmp_path / "OPERATOR_NOTES.md"
    stray.write_text("hours of notes\n")

    assert move_stray_aside(stray) is True
    assert not stray.exists()
    aside = tmp_path / "OPERATOR_NOTES.md.ralph-set-aside-1"
    assert aside.read_text() == "hours of notes\n", "the content must survive"


def test_an_untracked_directory_does_not_discard_a_proven_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One `__pycache__/` used to reject the stop, deterministically, every run."""
    from ralph.pipeline.conflict_resolution import stray_paths as stray_paths_module

    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "data.csv").write_text("1,2\n")

    def _untracked_probe(args: tuple[str, ...], *, cwd: Path, label: str) -> GitRunResult:
        assert args == ("ls-files", "--error-unmatch", "--", "scratch")
        assert cwd == tmp_path
        assert label == "git-stray-tracked"
        return GitRunResult(
            args=("git", *args),
            returncode=1,
            stdout="",
            stderr="scratch is untracked",
        )

    monkeypatch.setattr(stray_paths_module, "run_git", _untracked_probe)

    assert restore_one_unrequested_path(tmp_path, "scratch") is True
    assert (tmp_path / "scratch" / "data.csv").exists(), "and it is left alone"
