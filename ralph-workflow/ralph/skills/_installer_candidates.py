from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import GitCommandError, InvalidGitRepositoryError, Repo

from ralph.checked_accessors import optional_str
from ralph.git.scoped_auto_commit import _git_blob_sha
from ralph.skills._agent_paths import (
    project_sibling_skill_roots,
    project_skill_root,
)
from ralph.skills._content import _MANAGED_MARKER, BASELINE_SKILL_NAMES

if TYPE_CHECKING:
    from collections.abc import Callable


def _candidate_skill_paths(workspace_root: Path) -> list[str]:
    canonical = project_skill_root(workspace_root)
    candidates: list[str] = [_rel(canonical, "metadata.json", workspace_root)]
    for name in BASELINE_SKILL_NAMES:
        candidates.append(_rel(canonical / name, "SKILL.md", workspace_root))
        candidates.append(_rel(canonical / name, _MANAGED_MARKER, workspace_root))
    canonical_skills_dir = workspace_root / canonical.relative_to(workspace_root.resolve())
    if canonical_skills_dir.is_dir():
        for entry in canonical_skills_dir.iterdir():
            if entry.name in BASELINE_SKILL_NAMES or not (entry / _MANAGED_MARKER).exists():
                continue
            candidates.extend(_tracked_descendants(entry, workspace_root))
    for sibling in project_sibling_skill_roots(workspace_root):
        sibling_root = sibling.resolve(workspace_root)
        sibling_root_rel = str(sibling_root.relative_to(workspace_root))
        candidates.append(sibling_root_rel)
        candidates.extend(_rel(sibling_root, name, workspace_root) for name in BASELINE_SKILL_NAMES)
        for skill_name in BASELINE_SKILL_NAMES:
            canonical_skill_dir = canonical / skill_name
            sibling_skill_leaves = _canonical_skill_leaf_names(canonical_skill_dir, workspace_root)
            candidates.extend(
                _rel(sibling_root / skill_name, source_leaf, workspace_root)
                for source_leaf in sibling_skill_leaves
            )
        candidates.extend(_tracked_descendants(sibling_root, workspace_root))
    return candidates


def _canonical_skill_leaf_names(canonical_skill_dir: Path, workspace_root: Path) -> list[str]:
    _ = workspace_root
    leaves: set[str] = {"SKILL.md", _MANAGED_MARKER}
    if canonical_skill_dir.exists() or canonical_skill_dir.is_symlink():
        if canonical_skill_dir.is_file() or canonical_skill_dir.is_symlink():
            return sorted(leaves)
        try:
            for (
                dirpath,
                _dirnames,
                filenames,
            ) in os.walk(str(canonical_skill_dir), followlinks=False):
                base = Path(str(dirpath)).relative_to(canonical_skill_dir)
                for filename in filenames:
                    leaves.add(str(base / filename))
        except OSError:
            return sorted(leaves)
    return sorted(leaves)


def _tracked_descendants(root: Path, workspace_root: Path) -> list[str]:
    if not root.exists() and not root.is_symlink():
        return []
    try:
        try:
            repo_obj = Repo(str(workspace_root), search_parent_directories=False)
        except (InvalidGitRepositoryError, Exception):
            return _lexical_descendants(root, workspace_root)
        try:
            try:
                raw_output: object = repo_obj.git.ls_files("--", str(root))
                raw = optional_str(raw_output) or ""
            except (GitCommandError, OSError):
                return _lexical_descendants(root, workspace_root)
            paths = [line.strip() for line in raw.splitlines() if line.strip()]
            return sorted(set(paths))
        finally:
            close = cast("Callable[[], object] | None", getattr(repo_obj, "close", None))
            if callable(close):
                close()
    except ImportError:
        return _lexical_descendants(root, workspace_root)


def _lexical_descendants(root: Path, workspace_root: Path) -> list[str]:
    if not root.exists() and not root.is_symlink():
        return []
    if root.is_symlink() or root.is_file():
        try:
            return [str(root.relative_to(workspace_root))]
        except ValueError:
            return []
    paths: list[str] = []
    for (
        dirpath,
        _dirnames,
        filenames,
    ) in os.walk(root, followlinks=False):
        base_rel = Path(str(dirpath)).relative_to(workspace_root)
        for filename in filenames:
            file_path = base_rel / filename
            paths.append(str(file_path))
    return sorted(set(paths))


def _rel(absolute: Path, leaf: str, workspace_root: Path) -> str:
    leaf_path = absolute / leaf
    workspace_resolved = workspace_root.resolve()
    try:
        leaf_resolved = leaf_path.resolve(strict=False)
    except OSError:
        leaf_resolved = leaf_path
    with contextlib.suppress(ValueError):
        leaf_resolved.relative_to(workspace_resolved)
    return str(leaf_path.relative_to(workspace_root))


def _diff_written_paths(
    workspace_root: Path,
    candidate_paths: list[str],
    pre_contents: dict[str, str | None],
) -> list[str]:
    try:
        repo = Repo(workspace_root)
    except (InvalidGitRepositoryError, Exception):
        return []
    try:
        written: list[str] = []
        for path in candidate_paths:
            try:
                current_sha = _git_blob_sha(repo, path)
            except (OSError, GitCommandError):
                continue
            pre_sha = pre_contents.get(path)
            if pre_sha != current_sha:
                written.append(path)
        return written
    finally:
        close = cast("Callable[[], object] | None", getattr(repo, "close", None))
        if callable(close):
            close()
