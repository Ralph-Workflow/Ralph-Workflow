from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from git import Repo

from ralph.git.hardening import GIT_DIR_ENV_KEYS, drop_inherited_git_dir_env

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(120)]

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_TESTS_UNDER_BISECT = (
    "tests/test_inherited_git_dir_isolation.py::test_the_test_session_starts_without_an_inherited_git_dir",
    "tests/test_retained_resolution_handoff_foreign.py::"
    "test_foreign_reconciliation_completion_retains_initiating_feature_landing",
)


def _clean_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in GIT_DIR_ENV_KEYS}


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        env=_clean_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _linked_worktree_git_dir(tmp_path: Path) -> tuple[Path, str]:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(
        main,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "a",
    )
    _git(main, "worktree", "add", "-q", "--detach", str(tmp_path / "linked"))
    return main, _git(tmp_path / "linked", "rev-parse", "--absolute-git-dir")


def test_a_run_under_git_bisect_never_reinitializes_the_shared_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, linked_git_dir = _linked_worktree_git_dir(tmp_path)
    monkeypatch.setenv("GIT_DIR", linked_git_dir)
    fixture_repo = tmp_path / "fixture"
    fixture_repo.mkdir()

    assert drop_inherited_git_dir_env(os.environ) == ("GIT_DIR",)
    Repo.init(fixture_repo, initial_branch="main").close()

    assert _git(main, "config", "--file", ".git/config", "core.bare") == "false"
    assert (fixture_repo / ".git").is_dir()
    assert not any(key in os.environ for key in GIT_DIR_ENV_KEYS)


def test_the_suite_run_by_git_bisect_from_a_linked_worktree_leaves_the_shared_config_alone(
    tmp_path: Path,
) -> None:
    main, linked_git_dir = _linked_worktree_git_dir(tmp_path)
    config_before = (main / ".git" / "config").read_bytes()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "--no-header",
            "-m",
            "subprocess_e2e or not subprocess_e2e",
            f"--basetemp={tmp_path / 'nested'}",
            *_TESTS_UNDER_BISECT,
        ],
        cwd=_PACKAGE_ROOT,
        env={
            **{key: value for key, value in _clean_env().items() if not key.startswith("PYTEST_")},
            "GIT_DIR": linked_git_dir,
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=110,
    )

    assert (main / ".git" / "config").read_bytes() == config_before
    assert _git(main, "config", "--file", ".git/config", "core.bare") == "false"
    assert result.returncode == 0, (result.stdout + result.stderr)[-6000:]
