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
    assert is_ralph_workspace_path(".agent/tmp/rebase_conflict_resolution_prompt.md") is True
    assert is_ralph_workspace_path(".agent") is True
    assert is_ralph_workspace_path("src/agent_config.py") is False
    assert is_ralph_workspace_path("docs/.agentic.md") is False


def test_a_stray_file_is_moved_aside_not_destroyed(tmp_path: Path) -> None:
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
