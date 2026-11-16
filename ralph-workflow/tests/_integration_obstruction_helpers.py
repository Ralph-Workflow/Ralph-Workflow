from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from git import Repo

pytestmark = pytest.mark.subprocess_e2e

_PSEUDO_REFS = ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")

_REPO_CACHE: dict[Path, tuple[tuple[int, int], Repo]] = {}


def _resolve_git_dirs(root: Path) -> tuple[Path, Path]:
    git_path = root / ".git"
    git_dir = git_path
    if git_path.is_file():
        try:
            line = git_path.read_text(encoding="utf-8").strip()
            if line.startswith("gitdir: "):
                target = Path(line.removeprefix("gitdir: "))
                git_dir = target if target.is_absolute() else (root / target).resolve()
        except OSError:
            pass
    common_dir = git_dir
    commondir_file = git_dir / "commondir"
    if commondir_file.is_file():
        try:
            c = commondir_file.read_text(encoding="utf-8").strip()
            common_dir = (git_dir / c).resolve()
        except OSError:
            pass
    return git_dir, common_dir


def _git_dir(root: Path) -> Path:
    return _resolve_git_dirs(root)[0]


def _fast_branch_name(root: Path) -> str | None:
    git_dir, _ = _resolve_git_dirs(root)
    head_file = git_dir / "HEAD"
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content.removeprefix("ref: refs/heads/")
    except OSError:
        pass
    return None


def _fast_rev_parse(root: Path, ref_name: str) -> str | None:
    if len(ref_name) == 40 and all(c in "0123456789abcdefABCDEF" for c in ref_name):
        return ref_name
    git_dir, common_dir = _resolve_git_dirs(root)
    resolved: str | None = None
    try:
        if ref_name == "HEAD":
            head_content = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
            if head_content.startswith("ref: refs/heads/"):
                target_ref = (
                    common_dir / "refs" / "heads" / head_content.removeprefix("ref: refs/heads/")
                )
                if target_ref.is_file():
                    resolved = target_ref.read_text(encoding="utf-8").strip()
            elif len(head_content) == 40:
                resolved = head_content
        else:
            ref_path = (
                common_dir / ref_name
                if ref_name.startswith(("refs/heads/", "refs/remotes/"))
                else common_dir / "refs" / "heads" / ref_name
            )
            if ref_path.is_file():
                resolved = ref_path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if resolved is not None:
        return resolved
    try:
        return _repo(root).commit(ref_name).hexsha
    except Exception:
        return None


def _repo_identity(root: Path) -> tuple[int, int]:
    try:
        stat = _git_dir(root).stat()
    except OSError:
        return (0, 0)
    return (stat.st_dev, stat.st_ino)


def _repo(repo_root: Path) -> Repo:
    identity = _repo_identity(repo_root)
    cached = _REPO_CACHE.get(repo_root)
    if cached is not None:
        if cached[0] == identity:
            return cached[1]
        cached[1].close()
    repo = Repo(repo_root)
    _REPO_CACHE[repo_root] = (identity, repo)
    return repo


def close_cached_repos() -> None:
    for _identity, repo in _REPO_CACHE.values():
        repo.close()
    _REPO_CACHE.clear()


def _fast_verify_ref(repo_root: Path, ref_name: str) -> str | None:
    git_dir = _git_dir(repo_root)
    if ref_name in _PSEUDO_REFS:
        try:
            content = (git_dir / ref_name).read_text(encoding="utf-8").strip()
            if len(content) == 40:
                return content
        except OSError:
            pass
        stopped_sha = git_dir / "rebase-merge" / "stopped-sha"
        if ref_name == "REBASE_HEAD" and stopped_sha.is_file():
            try:
                content = stopped_sha.read_text(encoding="utf-8").strip()
                if len(content) == 40:
                    return content
            except OSError:
                pass
        return None
    return _fast_rev_parse(repo_root, ref_name)


def _completed(
    args: tuple[str, ...], stdout: str = "", *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=("git", *args), stdout=stdout, stderr=stderr, returncode=returncode
    )


def _blob_text(repo: Repo, revision: str, path: str) -> str:
    if revision == "":
        entry = repo.index.entries[(path, 0)]
        return repo.odb.stream(entry.binsha).read().decode()
    blob = repo.commit(revision).tree / path
    return blob.data_stream.read().decode()


def _is_ancestor(repo: Repo, ancestor: str, descendant: str) -> bool:
    wanted = repo.commit(ancestor).binsha
    pending = [repo.commit(descendant)]
    visited: set[bytes] = set()
    while pending:
        commit = pending.pop()
        if commit.binsha == wanted:
            return True
        if commit.binsha not in visited:
            visited.add(commit.binsha)
            pending.extend(commit.parents)
    return False


def _clone_template(template: Path, root: Path) -> None:
    cached = _REPO_CACHE.pop(root, None)
    if cached is not None:
        cached[1].close()
    shutil.rmtree(root)
    shutil.copytree(
        template,
        root,
        ignore=shutil.ignore_patterns("hooks", "*.sample", "description", "branches"),
    )
