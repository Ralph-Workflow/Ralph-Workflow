"""Remove git's ort residue without destroying the operator's own files.

When the ort merge backend cannot place both sides of a directory/file
conflict at the conflicted path, it parks one side beside it as
``<path>~<LABEL>``, where LABEL names the ref that side came from.
Those parked files are untracked, and leaving them behind makes the
next status read dirty for a resolution that actually succeeded.

Deleting them by glob, however, also deleted ``notes.md~`` and
``notes.md~4~`` -- an editor's backups -- and ``notes.md~draft``, a file
no side of the conflict ever mentioned. So a candidate is removed only
when its label is BOTH shaped like an ort label (non-empty, not an
editor backup suffix) and resolvable by git as a commit.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ralph.git.subprocess_runner import run_git

__all__ = ["is_ort_residue_name", "label_names_a_git_ref", "remove_ort_residue"]


def is_ort_residue_name(conflicted_name: str, candidate_name: str) -> bool:
    """Whether ``candidate_name`` is git's ort residue for ``conflicted_name``.

    ort parks a side under ``<path>~<LABEL>`` where LABEL is the ref it
    came from. An editor backup is ``<path>~`` or ``<path>~4~``, and
    deleting those destroys operator files that no side of the conflict
    ever mentioned -- which this glob was doing on every merge
    resolution and every rebase stop.
    """
    label = candidate_name[len(conflicted_name) + 1 :]
    if not label or label.endswith("~"):
        return False
    return bool(label.strip("0123456789"))


def label_names_a_git_ref(root: Path, label: str) -> bool:
    """Whether ``label`` is something git could have named a side after.

    ort names its residue after the ref or commit the side came from, so
    a label git cannot resolve was written by somebody else -- an
    operator's `notes.md~draft`, say -- and deleting it destroys a file
    no side of the conflict ever mentioned.
    """
    candidate = label.split(" ", maxsplit=1)[0]
    if not candidate:
        return False
    resolved = run_git(
        ("rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"),
        cwd=root,
        label="git-ort-residue-label",
    )
    return resolved.returncode == 0


def remove_ort_residue(root: Path, paths: tuple[str, ...]) -> bool:
    """Remove only untracked ``path~label`` files left by an ort D/F conflict."""
    for path in paths:
        candidate_parent = (root / path).parent
        try:
            candidates = tuple(
                candidate
                for candidate in candidate_parent.glob(f"{Path(path).name}~*")
                if is_ort_residue_name(Path(path).name, candidate.name)
                and label_names_a_git_ref(
                    root, candidate.name[len(Path(path).name) + 1 :]
                )
            )
        except OSError:
            return False
        for candidate in candidates:
            relative = candidate.relative_to(root).as_posix()
            tracked = run_git(
                ("ls-files", "--error-unmatch", "--", relative),
                cwd=root,
                label="git-ort-residue-tracked",
            )
            if tracked.returncode == 0:
                continue
            try:
                if candidate.is_dir():
                    # Not something to delete, and not a reason to throw
                    # away a resolution that has already been proven.
                    logger.info(
                        "conflict_resolution: leaving directory '{}' in place",
                        candidate.name,
                    )
                    continue
                candidate.unlink()
            except OSError:
                return False
    return True
