from __future__ import annotations

import os

import pytest

from ralph.git.hardening import GIT_DIR_ENV_KEYS, drop_inherited_git_dir_env

pytestmark = pytest.mark.timeout_seconds(5)


def test_inherited_repository_selection_variables_are_dropped() -> None:
    environ = {
        "GIT_DIR": "/repo/.git/worktrees/wt",
        "GIT_WORK_TREE": "/repo/wt",
        "GIT_INDEX_FILE": "/repo/.git/index",
        "GIT_COMMON_DIR": "/repo/.git",
        "GIT_EDITOR": "true",
        "PATH": "/usr/bin",
    }

    dropped = drop_inherited_git_dir_env(environ)

    assert dropped == ("GIT_COMMON_DIR", "GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE")
    assert environ == {"GIT_EDITOR": "true", "PATH": "/usr/bin"}


def test_the_test_session_starts_without_an_inherited_git_dir() -> None:
    assert not any(key in os.environ for key in GIT_DIR_ENV_KEYS)
