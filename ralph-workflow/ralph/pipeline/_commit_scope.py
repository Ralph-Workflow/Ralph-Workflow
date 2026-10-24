"""Commit scope resolution: which repository paths a commit artifact may stage.

The commit-message artifact may name the exact set of files the commit
is allowed to stage (``files``) or the set it must leave out
(``excluded_files``). Resolving that selection against the LIVE changed
set is the boundary where a malformed artifact could ask the executor to
stage a path outside the worktree, so the whole resolution -- path
normalization, de-duplication, the symlink / escape guard and the
exact-match check -- lives here as ONE cohesive unit.

Extracted from :mod:`ralph.pipeline.commit_executor` without any
behavior change: the helper names, their signatures and their semantics
are identical, and the executor re-exports them under the same private
names, so every existing call site and test keeps resolving them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class _CommitScopeResolution:
    include_paths: tuple[str, ...] | None


def _commit_include_paths_from_changed(
    payload: dict[str, object],
    changed_paths: list[str],
    repo_root: Path | None = None,
) -> list[str] | None:
    resolution = _resolve_commit_scope(payload, changed_paths, repo_root=repo_root)
    if resolution.include_paths is None:
        return None
    return list(resolution.include_paths)


def _resolve_commit_scope(
    payload: dict[str, object],
    changed_paths: list[str],
    repo_root: Path | None = None,
) -> _CommitScopeResolution:
    raw_files = payload.get("files")
    raw_excluded = payload.get("excluded_files")

    if not isinstance(raw_files, list) and not isinstance(raw_excluded, list):
        return _CommitScopeResolution(include_paths=None)

    changed = _dedupe_repo_relative_paths(changed_paths)
    if isinstance(raw_files, list):
        normalized_changed = {_normalize_repo_relative_path(path) for path in changed}
        include_paths: list[str] = []
        for raw_path in raw_files:
            if not isinstance(raw_path, str):
                continue
            normalized = _normalize_repo_relative_path(raw_path)
            _reject_symlink_in_commit_scope(normalized, repo_root)
            if normalized not in normalized_changed:
                raise ValueError(
                    "Commit artifact requested file "
                    f"'{normalized}' that is not part of the current changed set"
                )
            if normalized not in include_paths:
                include_paths.append(normalized)
        requested_paths = set(include_paths)
        if requested_paths != normalized_changed:
            raise ValueError(
                "Commit artifact file selection must exactly match the current changed set: "
                f"expected {sorted(normalized_changed)!r}, got {sorted(requested_paths)!r}"
            )
        return _CommitScopeResolution(include_paths=tuple(include_paths))
    if not isinstance(raw_excluded, list):
        return _CommitScopeResolution(include_paths=None)
    excluded: set[str] = set()
    for item in raw_excluded:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        normalized_excluded = _normalize_repo_relative_path(raw_path)
        _reject_symlink_in_commit_scope(normalized_excluded, repo_root)
        excluded.add(normalized_excluded)
    filtered_paths: tuple[str, ...] = tuple(
        path for path in changed if _normalize_repo_relative_path(path) not in excluded
    )
    return _CommitScopeResolution(include_paths=filtered_paths)


def _reject_symlink_in_commit_scope(normalized_path: str, repo_root: Path | None) -> None:
    """Reject ``normalized_path`` if it escapes ``repo_root`` via symlinks.

    Defense-in-depth: a malformed commit payload could include a path
    that is a symlink OR a path whose parent directory is a symlink
    chain pointing outside the worktree. ``_normalize_repo_relative_path``
    only checks the literal path string (absolute / ``..`` segments) and
    does not look at the filesystem. Without this guard, a symlink at
    a relative path (or any symlink in the parent chain) could stage a
    file outside the worktree when ``git add`` resolves the symlink
    target.

    Two checks are applied:

    1. **Literal-path symlink check** (defense-in-depth at the boundary):
       walks every parent directory AND the candidate path itself and
       rejects the first symlink found. Catches the obvious case where
       ``foo`` is a symlink in the worktree pointing somewhere else.

    2. **Resolved-path escape check** (defense-in-depth against parent
       symlink chains): resolves the candidate path with ``strict=False``
       (so broken symlinks also fire) and verifies the resolved
       location is still under ``repo_root``. Catches the parent-chain
       case where ``dir/file.txt`` is regular, but ``dir`` is a symlink
       pointing outside the worktree (the literal-path check above
       catches it too, but the resolved-path check provides a clean
       error message for the escape attempt).

    The checks are best-effort: a missing path is allowed through (the
    symlink won't fire if it doesn't exist; ``_stage_files`` will
    surface a real I/O error if needed).
    """
    if repo_root is None:
        return
    # An embedded NUL is named for what it is. Without this, ``resolve()``
    # below raises ``ValueError: embedded null byte``, the escape check
    # catches it, and the commit fails claiming the path is "outside
    # repository root" — a lie the agent cannot act on, while the artifact
    # is retained and the next pass fails the same way.
    if "\x00" in normalized_path:
        raise ValueError(
            f"Refusing to stage a path containing an embedded NUL in commit scope: "
            f"{normalized_path!r}"
        )
    repo_root_resolved = Path(repo_root).resolve(strict=False)
    candidate = repo_root / normalized_path
    if candidate.is_symlink():
        raise ValueError(f"Refusing to stage symlink in commit scope: {normalized_path!r}")
    for parent in candidate.parents:
        if parent == repo_root_resolved or parent == Path(repo_root):
            break
        if parent.is_symlink():
            raise ValueError(
                f"Refusing to stage path whose parent directory is a symlink "
                f"chain escape in commit scope: {normalized_path!r}"
            )
    try:
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(repo_root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"Refusing to stage path outside repository root in commit scope: {normalized_path!r}"
        ) from exc


def _dedupe_repo_relative_paths(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    for path in paths:
        normalized = _normalize_repo_relative_path(path)
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _normalize_repo_relative_path(raw_path: str) -> str:
    path = PurePosixPath(raw_path.strip())
    if not raw_path.strip():
        raise ValueError("Commit artifact paths must not be empty")
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"Commit artifact path '{raw_path}' must stay inside the repository root")
    normalized = path.as_posix()
    if normalized in {".", ""}:
        raise ValueError("Commit artifact paths must reference a concrete repository file")
    return normalized
