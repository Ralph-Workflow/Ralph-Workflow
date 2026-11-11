
from __future__ import annotations

from pathlib import Path

from loguru import logger

from ralph.git.subprocess_runner import run_git


def ancestry_state(repo_root: Path | str, ancestor: str, descendant: str) -> bool | None:
    if ancestor and ancestor == descendant:
        return True
    try:
        result = run_git(
            ("merge-base", "--is-ancestor", ancestor, descendant),
            cwd=Path(repo_root),
            label="git-is-ancestor",
        )
    except Exception as exc:
        logger.warning("ancestry query {} -> {} failed: {}", ancestor, descendant, exc)
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _nul_fields(text: str) -> list[str]:
    return [field for field in text.split("\0") if field]


_LS_TREE_META_FIELDS = 3


def _untracked_paths_identical_to(root: Path, target: str) -> list[str]:
    untracked = run_git(
        ("ls-files", "--others", "--exclude-standard", "-z"),
        cwd=root,
        label="git-untracked-obstructions",
    )
    candidates = _nul_fields(untracked.stdout) if untracked.returncode == 0 else []
    if not candidates:
        return []
    tree = run_git(
        ("ls-tree", "-r", "-z", "--full-tree", target, "--", *candidates),
        cwd=root,
        label="git-target-blobs",
    )
    target_blobs: dict[str, str] = {}
    for entry in _nul_fields(tree.stdout) if tree.returncode == 0 else []:
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) == _LS_TREE_META_FIELDS and parts[1] == "blob":
            target_blobs[path] = parts[2]
    if not target_blobs:
        return []
    paths = sorted(target_blobs)
    hashed = run_git(("hash-object", "--", *paths), cwd=root, label="git-hash-obstructions")
    oids = hashed.stdout.split() if hashed.returncode == 0 else []
    if len(oids) != len(paths):
        return []
    return [path for path, oid in zip(paths, oids, strict=True) if oid == target_blobs[path]]


def clear_untracked_merge_obstructions(repo_root: Path | str, target: str) -> tuple[str, ...]:
    root = Path(repo_root)
    identical = _untracked_paths_identical_to(root, target)
    removed: list[str] = []
    for path in identical:
        candidate = root / path
        try:
            candidate.unlink()
        except OSError as exc:
            logger.warning("could not remove merge obstruction {}: {}", path, exc)
            continue
        removed.append(path)
    if removed:
        logger.warning(
            "removed {} untracked file(s) identical to '{}' that would block the merge: {}",
            len(removed),
            target,
            ", ".join(removed),
        )
    return tuple(removed)


_REFUSAL_PATHS_SHOWN = 10

_OVERWRITE_REFUSALS: tuple[tuple[str, str], ...] = (
    ("untracked working tree files would be overwritten", "untracked files would be overwritten"),
    (
        "local changes to the following files would be overwritten",
        "local changes would be overwritten",
    ),
)


def merge_refusal(output: str) -> tuple[str, tuple[str, ...]] | None:
    lowered = output.lower()
    for needle, label in _OVERWRITE_REFUSALS:
        if needle not in lowered:
            continue
        paths = tuple(
            line.strip()
            for line in output.splitlines()
            if line.startswith(("\t", "    ")) and line.strip()
        )
        shown = ", ".join(paths[:_REFUSAL_PATHS_SHOWN])
        if len(paths) > _REFUSAL_PATHS_SHOWN:
            shown += f" (+{len(paths) - _REFUSAL_PATHS_SHOWN} more)"
        reason = f"merge refused: {label}: {shown}" if shown else f"merge refused: {label}"
        return reason, paths
    return None


__all__ = [
    "ancestry_state",
    "clear_untracked_merge_obstructions",
    "merge_refusal",
]
