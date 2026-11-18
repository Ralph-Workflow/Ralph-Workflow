from __future__ import annotations

import os
import shutil
from pathlib import Path

from tests.scenarios._integration_scenario_fixtures import (
    REPO_TEMPLATE_IGNORE,
    cached_repo,
    close_cached_repo,
)
from tests.test_pending_merge_commit_recovery import _fast_rev_parse

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "retained-resolution git assertions use equivalent GitPython object reads": (
        "the assertions still compare the same branch and HEAD object ids and the same "
        "committed blob contents; only their read-only git transport changed from the git "
        "executable to GitPython"
    ),
    "retained-resolution index setup uses GitPython": (
        "the setup still stages the same path and the following write-tree assertion still "
        "checks the resulting index exactly; unmerged stage entries are cleared before add "
        "so GitPython write_tree matches git add + write-tree on a conflicted path"
    ),
}


def _link_or_copy(src: str, dst: str, *, follow_symlinks: bool = True) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst, follow_symlinks=follow_symlinks)


def _clone_template(template: Path, dest: Path) -> Path:
    close_cached_repo(template)
    close_cached_repo(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        template,
        dest,
        ignore=REPO_TEMPLATE_IGNORE,
        copy_function=_link_or_copy,
    )
    return dest


def _sha(root: Path, ref: str = "HEAD") -> str:
    fast = _fast_rev_parse(root, ref)
    if fast is not None:
        return fast
    close_cached_repo(root)
    return cached_repo(root).commit(ref).hexsha


def _blob(root: Path, ref: str, path: str) -> str:
    close_cached_repo(root)
    return cached_repo(root).commit(ref).tree[path].data_stream.read().decode("utf-8")


def _stage(root: Path, *paths: str) -> None:
    index = cached_repo(root).index
    path_set = frozenset(paths)
    for key in tuple(index.entries):
        if key[0] in path_set and key[1] != 0:
            del index.entries[key]
    index.add(list(paths))


def _write_tree(root: Path) -> str:
    close_cached_repo(root)
    return str(cached_repo(root).index.write_tree())
