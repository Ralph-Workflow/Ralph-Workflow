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
    if repo_root is None:
        return
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
