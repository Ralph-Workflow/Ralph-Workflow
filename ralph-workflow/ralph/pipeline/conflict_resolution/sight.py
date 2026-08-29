"""Classify conflicts that must not spend the resolution chain."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ralph.git.merge import conflict_stage_entries, stage_paths

_GITLINK_MODE = "160000"
_TREE_MODE_PREFIX = "040000"


class ConflictSight(StrEnum):
    """What Ralph can do with one unmerged path before invoking an agent."""

    MECHANICAL = "mechanical"
    AGENT = "agent"
    OUT_OF_REACH = "out_of_reach"


def out_of_reach_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    """Return paths classified as out of reach."""
    return tuple(path for path, kind in kinds.items() if kind is ConflictSight.OUT_OF_REACH)


def mechanical_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    """Return paths Ralph can stage without an agent."""
    return tuple(path for path, kind in kinds.items() if kind is ConflictSight.MECHANICAL)


def classify_stage_map(
    stages: dict[int, tuple[str, str]],
    *,
    binary: bool,
) -> ConflictSight:
    """Classify one path from its index stages without reading git."""
    if binary:
        return ConflictSight.OUT_OF_REACH
    ours = stages.get(2)
    theirs = stages.get(3)
    if ours is None or theirs is None:
        # A one-sided stage set is a modify/delete (or an add on one
        # side): common, and a decision -- keep the edit, or accept the
        # removal -- that a resolver could make. It stays OUT OF REACH
        # anyway because nothing downstream can tell a made decision
        # from an untouched file: such a conflict carries no markers, so
        # `paths_with_conflict_markers` is empty either way, `git add`
        # stages the surviving file as readily as a deletion, and this
        # drain does not require completion evidence -- an agent that
        # exits having done nothing would silently land "keep", quietly
        # reversing a deletion the other side meant. Routing it here
        # needs a decision the round can actually verify first.
        return ConflictSight.AGENT if ours is None and theirs is None else ConflictSight.OUT_OF_REACH
    ours_mode, ours_blob = ours
    theirs_mode, theirs_blob = theirs
    if _is_tree(ours_mode) != _is_tree(theirs_mode):
        return ConflictSight.OUT_OF_REACH
    if ours_mode in {_GITLINK_MODE} or theirs_mode in {_GITLINK_MODE}:
        return _classify_gitlink(ours_mode, ours_blob, theirs_mode, theirs_blob, stages.get(1))
    return ConflictSight.MECHANICAL if ours_blob == theirs_blob else ConflictSight.AGENT


def _classify_gitlink(
    ours_mode: str,
    ours_blob: str,
    theirs_mode: str,
    theirs_blob: str,
    ancestor: tuple[str, str] | None,
) -> ConflictSight:
    if ours_mode == theirs_mode == _GITLINK_MODE and ancestor is not None:
        _ancestor_mode, ancestor_blob = ancestor
        if ancestor_blob == ours_blob == theirs_blob:
            return ConflictSight.MECHANICAL
    return ConflictSight.OUT_OF_REACH


def classify_unmerged_conflicts(root: Path, paths: tuple[str, ...]) -> dict[str, ConflictSight]:
    """Classify every unmerged path at ``root`` before spending the chain."""
    entries = conflict_stage_entries(root, paths)
    kinds: dict[str, ConflictSight] = {}
    for path in paths:
        kinds[path] = classify_stage_map(
            entries.get(path, {}),
            binary=_path_is_binary(root, path),
        )
    return kinds


def stage_mechanical_conflicts(root: Path, kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    """Stage identical-blob / matching-gitlink paths; return what was staged."""
    mechanical = mechanical_paths(kinds)
    if not mechanical:
        return ()
    if stage_paths(root, mechanical):
        return mechanical
    return ()


def _is_tree(mode: str) -> bool:
    return mode.startswith(_TREE_MODE_PREFIX[:2]) and mode == _TREE_MODE_PREFIX


def _path_is_binary(root: Path, relative: str) -> bool:
    try:
        payload = (root / relative).read_bytes()
    except OSError:
        return False
    return b"\0" in payload
