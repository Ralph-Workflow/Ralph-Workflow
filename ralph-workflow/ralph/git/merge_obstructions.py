"""Obstructions that stop an integration from landing, and how to clear them.

``git merge`` REFUSES to start -- exit 2, nothing merged, nothing for a
conflict resolver to repair -- when untracked files or uncommitted edits
sit where the target changes a path, and a rebase refuses a dirty tree.
Left alone, every such refusal strands the branch behind its integration
target. These primitives turn each refusal into something integration can
finish:

* ``clear_untracked_merge_obstructions`` -- removes untracked files whose
  bytes equal the target's copy (the merge would write the same file).
* ``ancestry_state`` -- three-valued ``merge-base --is-ancestor`` for the
  "branch contains the target" gate (``None`` = not proven).

Uncommitted work is never committed, stashed or discarded by integration:
only untracked copies byte-identical to the target's file are removed.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ralph.git.subprocess_runner import run_git


def ancestry_state(repo_root: Path | str, ancestor: str, descendant: str) -> bool | None:
    """Three-valued ``git merge-base --is-ancestor``.

    ``True``/``False`` are git's own answer (exit 0/1); ``None`` means the
    query failed (unknown ref, unreadable repository). Callers that gate
    on "the branch already contains the target" must treat ``None`` as
    "not proven", never as either answer.
    """
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


#: ``git ls-tree`` metadata is ``<mode> <type> <oid>``.
_LS_TREE_META_FIELDS = 3


def _untracked_paths_identical_to(root: Path, target: str) -> list[str]:
    """Untracked paths whose bytes equal the blob ``target`` tracks there."""
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
    """Delete untracked files that are byte-identical to ``target``'s copy.

    ``git merge`` refuses to start (exit 2, "untracked working tree files
    would be overwritten by merge") when an untracked file sits where the
    target tracks a path. When the untracked bytes equal the target's blob
    the merge would write exactly the same file, so removing it loses
    nothing and lets the integration land. Leftovers of an aborted merge
    or a resolver session are the usual source. Files whose content
    differs are never touched: they are genuine local work, and the merge
    refusal that follows names them. Returns the removed paths.
    """
    root = Path(repo_root)
    identical = _untracked_paths_identical_to(root, target)
    removed: list[str] = []
    for path in identical:
        candidate = root / path
        try:
            # filesystem-write-ok: removes an untracked file whose bytes the target already holds
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
    """Why ``git merge`` refused to start, and the paths it named.

    ``None`` when the output is not an overwrite refusal (an ordinary
    conflict, or a failure this module cannot clear).
    """
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
