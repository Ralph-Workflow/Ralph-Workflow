"""Backup-reference lifecycle for auto-integrate attempts."""

from __future__ import annotations

import hashlib
import os
import re
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from pathlib import Path

#: ``git ls-tree`` rows are ``<mode> <type> <object>``.
_LS_TREE_META_FIELDS = 3


def qualified_backup_ref(owning_worktree: str | None, pre_feature_sha: str) -> str:
    """Worktree-qualified backup-ref name for one integration operation.

    Two linked worktrees that start from the same pre-feature SHA would
    otherwise share a single ``refs/rebase-backup/`` ref, letting one
    operation's cleanup destroy the other's only recovery ref. The
    owning-worktree hash makes the name per-operation. Deterministic in
    ``owning_worktree`` (including ``None``) so recovery can recompute
    the exact name from the durable record without schema changes.
    """
    wt8 = hashlib.sha256(str(owning_worktree).encode("utf-8")).hexdigest()[:8]
    return f"refs/rebase-backup/{wt8}-{pre_feature_sha[:8]}-{pre_feature_sha}"


def create_rebase_backup_ref(
    root: Path, pre_feature_sha: str | None, owning_worktree: str | None = None
) -> str | None:
    """Create the fail-closed backup ref that protects an in-flight feature tip."""
    if pre_feature_sha is None:
        return None
    # The operating worktree IS the owning worktree at this seam; the
    # str(root) fallback mirrors the ``record.owning_worktree or str(root)``
    # convention in _owned_landing_reintegration, so recovery recomputes
    # the identical name even before U4b threads record.owning_worktree.
    backup_name = qualified_backup_ref(
        owning_worktree if owning_worktree is not None else str(root), pre_feature_sha
    )
    try:
        result = run_git(
            ("update-ref", backup_name, pre_feature_sha),
            cwd=root,
            label="auto-integrate:backup-ref-create",
        )
    except Exception as exc:
        raise RuntimeError(
            "auto_integrate: B11/E5 backup-ref creation raised unexpectedly; "
            f"refusing to start without recovery reachability: {exc}"
        ) from exc
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout).strip()[:200]
        raise RuntimeError(
            "auto_integrate: B11/E5 backup-ref creation failed "
            f"(rc={result.returncode}): {stderr}; refusing to start"
        )
    return backup_name


def create_stale_resolution_snapshot(
    root: Path,
    feature_name: str | None,
    head_sha: str,
    owning_worktree: str | None = None,
) -> tuple[str, str]:
    """Preserve the current index and untracked work in a durable backup ref.

    The returned tuple is ``(backup_ref, feature_sha)``.  ``backup_ref``
    resolves to a commit whose tree contains the staged index plus every
    non-ignored untracked path.  ``feature_sha`` is the commit that also
    contains any unstaged tracked edits; when the staged and worktree
    trees are identical, both values are the same commit.  When the two
    trees differ, the backup ref is a two-parent commit whose second
    parent is the worktree commit, so both variants of the file stay
    reachable from the one ref.

    The current index and worktree are never changed.  Git's alternate
    ``index.file`` configuration gives the snapshot an isolated index,
    while ``read-tree --index-output`` seeds that index from the real
    staged tree without touching the caller's index.
    """
    if not head_sha:
        raise RuntimeError("auto_integrate: stale-resolution snapshot requires HEAD")

    head_tree = _snapshot_head_tree(root, head_sha)
    staged_entries = _snapshot_tree_entries(
        root,
        _snapshot_tree(root, label="auto-integrate:stale-snapshot-index"),
    )
    worktree_entries = _snapshot_worktree_entries(root, head_tree)
    untracked_entries = _snapshot_untracked_entries(root)
    snapshot_staged_tree = _snapshot_build_tree(
        root,
        [*staged_entries, *untracked_entries],
    )
    snapshot_worktree_tree = _snapshot_build_tree(
        root,
        [*worktree_entries, *untracked_entries],
    )

    if snapshot_staged_tree == snapshot_worktree_tree:
        if snapshot_staged_tree == head_tree:
            feature_sha = head_sha
            snapshot_sha = head_sha
        else:
            feature_sha = _snapshot_commit(root, snapshot_staged_tree, (head_sha,))
            snapshot_sha = feature_sha
    else:
        worktree_commit = _snapshot_commit(root, snapshot_worktree_tree, (head_sha,))
        feature_sha = worktree_commit
        snapshot_sha = _snapshot_commit(
            root,
            snapshot_staged_tree,
            (head_sha, worktree_commit),
        )

    backup_ref = _stale_resolution_ref_name(
        feature_name=feature_name,
        head_sha=head_sha,
        staged_tree=snapshot_staged_tree,
        worktree_tree=snapshot_worktree_tree,
        owning_worktree=owning_worktree or str(root),
    )
    created = run_git(
        ("update-ref", backup_ref, snapshot_sha),
        cwd=root,
        label="auto-integrate:stale-snapshot-ref-create",
    )
    if created.returncode != 0:
        stderr = (created.stderr or created.stdout).strip()[:200]
        raise RuntimeError(
            "auto_integrate: stale-resolution backup-ref creation failed "
            f"(rc={created.returncode}): {stderr}; refusing to clear stale intent"
        )
    return backup_ref, feature_sha


def _snapshot_tree(root: Path, *, label: str) -> str:
    result = run_git(("write-tree",), cwd=root, label=label)
    tree = result.stdout.strip()
    if result.returncode != 0 or not tree:
        stderr = (result.stderr or result.stdout).strip()[:200]
        raise RuntimeError(f"auto_integrate: snapshot tree is unreadable: {stderr}")
    return tree


def _snapshot_head_tree(root: Path, head_sha: str) -> str:
    result = run_git(
        ("rev-parse", "--verify", f"{head_sha}^{{tree}}"),
        cwd=root,
        label="auto-integrate:stale-snapshot-head-tree",
    )
    tree = result.stdout.strip()
    if result.returncode != 0 or not tree:
        stderr = (result.stderr or result.stdout).strip()[:200]
        raise RuntimeError(f"auto_integrate: snapshot HEAD tree is unreadable: {stderr}")
    return tree


def _snapshot_untracked_paths(root: Path) -> tuple[str, ...]:
    result = run_git(
        ("ls-files", "--others", "--exclude-standard", "-z"),
        cwd=root,
        label="auto-integrate:stale-snapshot-untracked",
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout).strip()[:200]
        raise RuntimeError(f"auto_integrate: snapshot untracked paths are unreadable: {stderr}")
    return tuple(
        path for path in result.stdout.split("\0") if path and not path.startswith(".agent/")
    )


def _snapshot_tree_entries(root: Path, tree: str) -> list[tuple[str, str, str, str]]:
    """Return the ``(path, mode, type, oid)`` rows of a tree, recursively."""
    result = run_git(
        ("ls-tree", "-r", "-z", tree),
        cwd=root,
        label="auto-integrate:stale-snapshot-ls-tree",
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout).strip()[:200]
        raise RuntimeError(f"auto_integrate: snapshot tree rows are unreadable: {stderr}")
    entries: list[tuple[str, str, str, str]] = []
    for row in result.stdout.split("\0"):
        if not row:
            continue
        meta, _, path = row.partition("\t")
        fields = meta.split()
        if len(fields) != _LS_TREE_META_FIELDS:
            continue
        mode, kind, oid = fields
        entries.append((path, mode, kind, oid))
    return entries


def _snapshot_worktree_entries(
    root: Path,
    head_tree: str,
) -> list[tuple[str, str, str, str]]:
    """Return the tracked worktree rows without touching the index.

    ``git stash create`` builds a commit of the current worktree state
    WITHOUT storing it in ``refs/stash`` and WITHOUT changing the index
    or the working tree, so the snapshot never has to stage the user's
    work to read it.
    """
    created = run_git(
        ("stash", "create", "ralph stale-resolution work snapshot"),
        cwd=root,
        label="auto-integrate:stale-snapshot-stash-create",
    )
    if created.returncode != 0:
        stderr = (created.stderr or created.stdout).strip()[:200]
        raise RuntimeError(f"auto_integrate: snapshot worktree state is unreadable: {stderr}")
    stash_sha = created.stdout.strip()
    if not stash_sha:
        return _snapshot_tree_entries(root, head_tree)
    tree = _snapshot_head_tree(root, stash_sha)
    return _snapshot_tree_entries(root, tree)


def _snapshot_untracked_entries(root: Path) -> list[tuple[str, str, str, str]]:
    """Return the untracked rows (mode, type, oid) the snapshot must keep."""
    return [
        (path, *_snapshot_untracked_blob(root, path)) for path in _snapshot_untracked_paths(root)
    ]


def _snapshot_untracked_blob(root: Path, path: str) -> tuple[str, str, str]:
    """Return the ``(mode, type, oid)`` of one untracked path."""
    absolute = root / path
    if absolute.is_symlink():
        target = os.fsencode(absolute.readlink())
        result = run_git(
            ("hash-object", "-w", "--stdin"),
            cwd=root,
            label="auto-integrate:stale-snapshot-hash-symlink",
            options=GitRunOptions(input_data=target),
        )
        oid = result.stdout.strip()
        if result.returncode != 0 or not oid:
            stderr = (result.stderr or result.stdout).strip()[:200]
            raise RuntimeError(f"auto_integrate: snapshot could not hash {path}: {stderr}")
        return ("120000", "blob", oid)
    result = run_git(
        ("hash-object", "-w", "--", path),
        cwd=root,
        label="auto-integrate:stale-snapshot-hash-untracked",
    )
    oid = result.stdout.strip()
    if result.returncode != 0 or not oid:
        stderr = (result.stderr or result.stdout).strip()[:200]
        raise RuntimeError(f"auto_integrate: snapshot could not hash {path}: {stderr}")
    mode = "100755" if os.access(absolute, os.X_OK) else "100644"
    return (mode, "blob", oid)


def _snapshot_build_tree(
    root: Path,
    entries: list[tuple[str, str, str, str]],
) -> str:
    """Build one tree object from flat ``(path, mode, type, oid)`` rows.

    ``git mktree`` accepts a single directory level, so nested paths are
    folded bottom-up: each directory becomes its own tree object and the
    parent level is written with the resulting tree id.
    """
    groups: dict[str, list[tuple[str, str, str, str]]] = {}
    for path, mode, kind, oid in entries:
        head, _, rest = path.partition("/")
        groups.setdefault(head, []).append((rest, mode, kind, oid))
    rows: list[tuple[str, str, str, str]] = []
    for name, items in groups.items():
        rest, mode, kind, oid = items[0]
        if rest:
            subtree = _snapshot_build_tree(root, items)
            rows.append(("040000", "tree", subtree, name))
        else:
            rows.append((mode, kind, oid, name))
    payload = b"".join(f"{mode} {kind} {oid}\t{name}\0".encode() for mode, kind, oid, name in rows)
    result = run_git(
        ("mktree", "-z"),
        cwd=root,
        label="auto-integrate:stale-snapshot-mktree",
        options=GitRunOptions(input_data=payload),
    )
    tree = result.stdout.strip()
    if result.returncode != 0 or not tree:
        stderr = (result.stderr or result.stdout).strip()[:200]
        raise RuntimeError(f"auto_integrate: snapshot tree could not be written: {stderr}")
    return tree


def _snapshot_commit(root: Path, tree: str, parents: tuple[str, ...]) -> str:
    args = [*COMMIT_PIN_CONFIG_ARGS, "commit-tree", tree]
    for parent in parents:
        args.extend(("-p", parent))
    result = run_git(
        args,
        cwd=root,
        label="auto-integrate:stale-snapshot-commit",
        options=GitRunOptions(
            env={
                "GIT_AUTHOR_NAME": "Ralph Workflow",
                "GIT_AUTHOR_EMAIL": "noreply@ralphworkflow.com",
                "GIT_COMMITTER_NAME": "Ralph Workflow",
                "GIT_COMMITTER_EMAIL": "noreply@ralphworkflow.com",
            },
            input_data="ralph stale-resolution work snapshot\n",
        ),
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or not commit:
        stderr = (result.stderr or result.stdout).strip()[:200]
        raise RuntimeError(f"auto_integrate: snapshot commit could not be created: {stderr}")
    return commit


def _stale_resolution_ref_name(
    *,
    feature_name: str | None,
    head_sha: str,
    staged_tree: str,
    worktree_tree: str,
    owning_worktree: str,
) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "-", feature_name or "detached").strip("-._")
    component = (component or "detached")[:48]
    worktree_id = hashlib.sha256(owning_worktree.encode("utf-8")).hexdigest()[:8]
    return (
        f"refs/ralph-backup/{component}-{head_sha[:8]}-"
        f"{staged_tree[:8]}-{worktree_tree[:8]}-{worktree_id}"
    )


def delete_rebase_backup_ref(root: Path, backup_ref: str | None) -> None:
    """Best-effort delete of a verified attempt's backup ref."""
    if backup_ref is None:
        return
    try:
        result = run_git(
            ("update-ref", "-d", backup_ref),
            cwd=root,
            label="auto-integrate:backup-ref-delete",
        )
    except Exception as exc:  # pragma: no cover -- defensive logging
        logger.warning("auto_integrate: backup-ref deletion failed: {}", exc)
        return
    if result.returncode not in (0, 1):
        logger.warning("auto_integrate: backup-ref deletion failed (rc={})", result.returncode)
