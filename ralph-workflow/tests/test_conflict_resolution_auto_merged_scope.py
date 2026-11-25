from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo
from git.exc import GitCommandError

from ralph.git.git_command_queue import route_git_method
from ralph.pipeline.conflict_resolution.auto_merged_paths import both_sides_changed_paths
from ralph.pipeline.conflict_resolution.prompt import render_conflict_prompt
from ralph.pipeline.conflict_resolution.rebase_loop import (
    RebaseStop,
    resolution_scope,
    resolve_rebase_in_progress,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_ACTOR = Actor("Ralph Test", "test@ralph.internal")
_LIB = "def keep():\n    return 1\n\n\ndef middle():\n    return 2\n\n\ndef tail():\n    return 3\n"


def _commit(repo: Repo, root: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    route_git_method(repo, "add", ("-A",))
    route_git_method(repo, "commit", ("-q", "-m", message))
    return str(route_git_method(repo, "rev_parse", ("HEAD",))).strip()


def _paused_rebase(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "project"
    root.mkdir()
    repo = Repo.init(root, initial_branch="main")
    with repo.config_writer() as config:
        config.set_value("user", "name", _ACTOR.name)
        config.set_value("user", "email", _ACTOR.email)
    _commit(repo, root, {"lib.py": _LIB, "conflict.txt": "base\n", "other.txt": "x\n"}, "base")
    route_git_method(repo, "checkout", ("-q", "-b", "feature"))
    feature = _commit(
        repo,
        root,
        {
            "lib.py": _LIB.replace("def keep():", "def decode():\n    return 0\n\n\ndef keep():"),
            "conflict.txt": "feature\n",
        },
        "feature adds decode at the top",
    )
    route_git_method(repo, "checkout", ("-q", "main"))
    _commit(
        repo,
        root,
        {
            "lib.py": _LIB + "\n\ndef decode():\n    return 0\n",
            "conflict.txt": "main\n",
            "other.txt": "y\n",
        },
        "main adds decode at the bottom",
    )
    route_git_method(repo, "checkout", ("-q", "feature"))
    with contextlib.suppress(GitCommandError):
        route_git_method(repo, "rebase", ("main",))
    return root, feature


def test_files_both_sides_changed_that_git_merged_cleanly_are_found(tmp_path: Path) -> None:
    root, feature = _paused_rebase(tmp_path)
    merged = (root / "lib.py").read_text(encoding="utf-8")
    assert merged.count("def decode():") == 2, "git's clean merge duplicated the definition"

    assert both_sides_changed_paths(root, feature) == ("conflict.txt", "lib.py")


def test_the_resolver_may_edit_auto_merged_files_but_nothing_else() -> None:
    stop = RebaseStop(
        sha="abc",
        subject="feat",
        conflicted_files=("conflict.txt",),
        stop_index=1,
        stop_cap=10,
        auto_merged_files=("lib.py",),
    )

    assert resolution_scope(stop) == frozenset({"conflict.txt", "lib.py"})


def test_the_prompt_lists_auto_merged_files_as_editable(tmp_path: Path) -> None:
    prompt_path = render_conflict_prompt(
        root=tmp_path,
        target="main",
        conflicted_paths=("conflict.txt",),
        auto_merged_paths=("lib.py",),
        round_index=1,
        round_cap=3,
        surviving_marker_paths=(),
        replaying_commit_sha="0123456789abcdef",
        replaying_commit_subject="feat: replayed",
        stop_index=1,
        stop_cap=10,
    )
    assert prompt_path is not None
    rendered = prompt_path.read_text(encoding="utf-8")

    assert "## Auto-merged files" in rendered
    assert "- `lib.py`" in rendered
    assert "duplicate" in rendered


def test_the_prompt_never_demands_a_clean_build_it_forbids_reaching(tmp_path: Path) -> None:
    prompt_path = render_conflict_prompt(
        root=tmp_path,
        target="main",
        conflicted_paths=("conflict.txt",),
        round_index=1,
        round_cap=3,
        surviving_marker_paths=(),
        replaying_commit_sha="0123456789abcdef",
        replaying_commit_subject="feat: replayed",
        stop_index=1,
        stop_cap=10,
    )
    assert prompt_path is not None
    rendered = prompt_path.read_text(encoding="utf-8")

    assert "build and pass checks cleanly before declaring" not in rendered
    assert "outside the files you may edit" in rendered


def _fix_both(root: Path, _target: str, stop: RebaseStop) -> bool:
    (root / "conflict.txt").write_text("feature\n", encoding="utf-8")
    lib = (root / "lib.py").read_text(encoding="utf-8")
    (root / "lib.py").write_text(
        lib.replace("def decode():\n    return 0\n\n\ndef keep():", "def keep():", 1),
        encoding="utf-8",
    )
    assert "lib.py" in stop.auto_merged_files
    return True


def _landed(root: Path, path: str) -> str:
    repo = Repo(root)
    return str(route_git_method(repo, "show", (f"HEAD:{path}",)))


def test_a_broken_clean_merge_repaired_by_the_resolver_lands_repaired(tmp_path: Path) -> None:
    root, _feature = _paused_rebase(tmp_path)

    assert resolve_rebase_in_progress(root, "main", _fix_both) is True

    assert not (root / ".git" / "rebase-merge").exists()
    assert _landed(root, "lib.py").count("def decode():") == 1
    assert _landed(root, "conflict.txt").strip() == "feature"


def test_an_edit_outside_the_resolution_scope_never_lands(tmp_path: Path) -> None:
    root, _feature = _paused_rebase(tmp_path)

    def resolve_and_stray(root: Path, target: str, stop: RebaseStop) -> bool:
        (root / "other.txt").write_text("stray edit\n", encoding="utf-8")
        return _fix_both(root, target, stop)

    assert resolve_rebase_in_progress(root, "main", resolve_and_stray) is True

    assert _landed(root, "other.txt").strip() == "y"
    assert _landed(root, "lib.py").count("def decode():") == 1
