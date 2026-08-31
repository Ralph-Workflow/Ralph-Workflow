"""Resolve the rebase stops that have exactly one defensible answer.

Two conflict shapes carry their own resolution: a stop where every
conflicted path differs only in its FILE MODE, and a stop where every
conflicted path is a GITLINK whose two sides are ancestor and
descendant of one another. Neither needs an agent, and spending a
resolution chain on them wasted the chain on a question git had already
answered.

The rule both branches obey is all-or-nothing: a stop is resolved here
only when EVERY one of its conflicted paths fits the same shape, and
the whole stop is staged through a single ``git update-index``. A mixed
or unreadable stop falls through unchanged to the resolver/endpoint-merge
ladder, so this module can only ever remove work from that ladder, never
half-resolve a stop and hand it on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import conflict_stage_entries
from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from ralph.pipeline.conflict_resolution.rebase_stop import RebaseStop

__all__ = ["try_deterministic_resolution"]

#: git's index stages for the two sides of a conflict: stage 2 is
#: "ours" (the commit being replayed onto) and stage 3 is "theirs" (the
#: commit being replayed). Stage 1 is the merge base, read only by
#: :func:`_resolve_mode_only` to tell which side actually changed.
_CONFLICT_STAGE_OURS = 2
_CONFLICT_STAGE_THEIRS = 3


def try_deterministic_resolution(root: Path, stop: RebaseStop) -> bool:
    """Resolve a uniformly mode-only or descendant-gitlink stop, if safe.

    Mixed or unreadable stops deliberately fall through unchanged to the
    existing resolver/endpoint-merge ladder; this helper never resolves only
    part of a stop.
    """
    try:
        entries = conflict_stage_entries(root, stop.conflicted_files)
        if any(
            _CONFLICT_STAGE_OURS not in entries.get(path, {})
            or _CONFLICT_STAGE_THEIRS not in entries[path]
            for path in stop.conflicted_files
        ):
            return False
        stages = [entries[path] for path in stop.conflicted_files]
        if all(
            stage[_CONFLICT_STAGE_OURS][0] == stage[_CONFLICT_STAGE_THEIRS][0] == "160000"
            for stage in stages
        ):
            return _resolve_gitlinks(root, stop.conflicted_files, stages)
        if not all(
            stage[_CONFLICT_STAGE_OURS][1] == stage[_CONFLICT_STAGE_THEIRS][1]
            and {
                stage[_CONFLICT_STAGE_OURS][0],
                stage[_CONFLICT_STAGE_THEIRS][0],
            }
            == {"100644", "100755"}
            for stage in stages
        ):
            return False
        return _resolve_mode_only(root, stop.conflicted_files, stages)
    except Exception as exc:
        logger.warning("conflict_resolution: deterministic resolution declined: {}", exc)
        return False


def _resolve_gitlinks(
    root: Path,
    paths: tuple[str, ...],
    stages: list[dict[int, tuple[str, str]]],
) -> bool:
    """Pick the descendant for every locally-verifiable gitlink conflict."""
    chosen: list[tuple[str, str]] = []
    for path, stage in zip(paths, stages, strict=True):
        ours = stage[_CONFLICT_STAGE_OURS][1]
        theirs = stage[_CONFLICT_STAGE_THEIRS][1]
        submodule = root / path
        if (
            run_git(
                ("-C", str(submodule), "rev-parse", "--git-dir"),
                cwd=root,
                label="git-gitlink-dir",
            ).returncode
            != 0
        ):
            return False
        if any(
            run_git(
                ("-C", str(submodule), "cat-file", "-e", sha),
                cwd=root,
                label="git-gitlink-object",
            ).returncode
            != 0
            for sha in (ours, theirs)
        ):
            return False
        ours_before_theirs = run_git(
            ("-C", str(submodule), "merge-base", "--is-ancestor", ours, theirs),
            cwd=root,
            label="git-gitlink-ancestor",
        ).returncode
        theirs_before_ours = run_git(
            ("-C", str(submodule), "merge-base", "--is-ancestor", theirs, ours),
            cwd=root,
            label="git-gitlink-ancestor",
        ).returncode
        if ours_before_theirs == 0:
            chosen.append((path, theirs))
        elif theirs_before_ours == 0:
            chosen.append((path, ours))
        else:
            return False
    return _stage_deterministic_entries(
        root,
        (("160000", sha, path) for path, sha in chosen),
        label="git-gitlink-resolve",
    )


def _resolve_mode_only(
    root: Path,
    paths: tuple[str, ...],
    stages: list[dict[int, tuple[str, str]]],
) -> bool:
    """Prefer target mode unless the feature changed it from the base."""
    chosen = (
        (
            feature_mode if stage.get(1, ("", ""))[0] == target_mode else target_mode,
            blob,
            path,
        )
        for path, stage in zip(paths, stages, strict=True)
        for target_mode, blob in (stage[_CONFLICT_STAGE_OURS],)
        for feature_mode in (stage[_CONFLICT_STAGE_THEIRS][0],)
    )
    return _stage_deterministic_entries(
        root,
        chosen,
        label="git-mode-only-resolve",
    )


def _stage_deterministic_entries(
    root: Path,
    entries: Iterable[tuple[str, str, str]],
    *,
    label: str,
) -> bool:
    """Stage an entire deterministic stop through one atomic index update.

    ``git update-index`` holds its lock until all cacheinfo records validate,
    so a non-zero exit leaves every conflicted path untouched for the normal
    resolver. Issuing one command prevents a later failure from partially
    resolving a stop.
    """
    args: list[str] = ["update-index"]
    for mode, blob, path in entries:
        args.extend(("--cacheinfo", f"{mode},{blob},{path}"))
    return run_git(tuple(args), cwd=root, label=label).returncode == 0
