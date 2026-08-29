"""Classify conflicts that must not spend the resolution chain."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ralph.git.merge import (
    conflict_stage_entries,
    paths_with_conflict_markers,
    stage_paths,
)

_GITLINK_MODE = "160000"
_TREE_MODE_PREFIX = "040000"


class ConflictSight(StrEnum):
    """What Ralph can do with one unmerged path before invoking an agent."""

    MECHANICAL = "mechanical"
    AGENT = "agent"
    AGENT_DECISION = "agent_decision"
    OUT_OF_REACH = "out_of_reach"


def out_of_reach_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    """Return paths classified as out of reach."""
    return tuple(path for path, kind in kinds.items() if kind is ConflictSight.OUT_OF_REACH)


def declared_decision_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    """Return paths whose resolution is a decision, not an edit.

    A modify/delete carries no conflict markers, so "the markers are
    gone" -- the evidence every other conflict is judged by -- is true
    of it before anyone touches it. The resolver must therefore SAY it
    decided, and the round demands completion evidence for these paths
    rather than crediting a file nobody looked at.
    """
    return tuple(path for path, kind in kinds.items() if kind is ConflictSight.AGENT_DECISION)


def mechanical_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    """Return paths Ralph can stage without an agent."""
    return tuple(path for path, kind in kinds.items() if kind is ConflictSight.MECHANICAL)


def classify_stage_map(
    stages: dict[int, tuple[str, str]],
    *,
    binary: bool,
    has_markers: bool = True,
) -> ConflictSight:
    """Classify one path from its index stages without reading git.

    ``has_markers`` is what git actually left in the worktree, and it
    decides whether the resolution will be READABLE. A NUL-byte probe
    is not enough on its own: ``conflict-marker-size``, ``-merge``,
    ``binary`` and ``merge=binary`` are all documented gitattributes
    that make git write a whole side instead of a marked-up hunk, so an
    ASCII lockfile can be every bit as markerless as a PNG. Judged as an
    ordinary conflict, such a path passes the marker scan the moment it
    is created -- and an agent that did nothing was credited with
    resolving it.
    """
    if binary:
        # A binary conflict carries no markers, so Ralph cannot read the
        # resolution off the file -- but the resolver session holds
        # write_file, edit_file and delete_path, so it CAN carry one out.
        # Calling it unreachable escalated the whole set on sight, which
        # meant an ordinary text conflict beside a PNG was never offered
        # to anyone, on every run, forever.
        return ConflictSight.AGENT_DECISION
    ours = stages.get(2)
    theirs = stages.get(3)
    if ours is None and theirs is None:
        return ConflictSight.AGENT
    if ours is None or theirs is None:
        return _classify_one_sided(ours if ours is not None else theirs)
    ours_mode, ours_blob = ours
    theirs_mode, theirs_blob = theirs
    if _is_tree(ours_mode) != _is_tree(theirs_mode):
        # A file on one side, a directory on the other. The session can
        # create directories and delete paths, so this is a decision it
        # can carry out -- and one Ralph cannot read off a file.
        return ConflictSight.AGENT_DECISION
    if ours_mode in {_GITLINK_MODE} or theirs_mode in {_GITLINK_MODE}:
        return _classify_gitlink(ours_mode, ours_blob, theirs_mode, theirs_blob, stages.get(1))
    return _classify_two_sided_blob(ours_blob, theirs_blob, has_markers=has_markers)


def _classify_two_sided_blob(
    ours_blob: str, theirs_blob: str, *, has_markers: bool
) -> ConflictSight:
    """Classify a conflict where both sides have a blob."""
    if ours_blob == theirs_blob:
        return ConflictSight.MECHANICAL
    # Both sides changed it. If git left no markers, nothing about the
    # file will show whether a resolution happened, so it is a declared
    # decision rather than an ordinary edit.
    return ConflictSight.AGENT if has_markers else ConflictSight.AGENT_DECISION


def _classify_one_sided(present: tuple[str, str] | None) -> ConflictSight:
    """Classify a conflict where one side has no blob at all.

    Exactly one of stages 2 and 3 missing is a modify/delete: one side
    edited the file, the other removed it. It is one of the commonest
    conflicts there is and the decision it needs -- keep the edit, or
    accept the removal -- is exactly what a resolver is for, so
    escalating it on sight spent no agent on work an agent could do.

    It is a DECISION rather than an edit because git leaves the
    surviving version in the worktree with no conflict markers: the
    marker scan that judges every other conflict is already satisfied,
    so the round has to require the resolver to declare completion
    instead. A surviving side that is a submodule or a directory stays
    out of reach; those are not decisions a text editor can carry out.
    """
    if present is None:
        return ConflictSight.OUT_OF_REACH
    mode, _blob = present
    if mode == _GITLINK_MODE:
        # A submodule pointer. git is denied to the resolution session,
        # so there is no tool with which it could write a gitlink.
        return ConflictSight.OUT_OF_REACH
    return ConflictSight.AGENT_DECISION


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
    marked = set(paths_with_conflict_markers(root, paths))
    kinds: dict[str, ConflictSight] = {}
    for path in paths:
        kinds[path] = classify_stage_map(
            entries.get(path, {}),
            binary=_path_is_binary(root, path),
            has_markers=path in marked,
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
