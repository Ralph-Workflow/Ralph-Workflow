from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ralph.pipeline.conflict_resolution.session import ResolutionSession

from ralph.git.merge import (
    ConflictStageEntries,
    conflict_stage_entries,
    paths_with_conflict_markers,
    stage_paths,
)
from ralph.pipeline.conflict_resolution._conflict_classification import ConflictClassification
from ralph.pipeline.conflict_resolution._operator_command import operator_command_for_paths

__all__ = [
    "ConflictClassification",
    "ConflictSight",
    "classify_stage_map",
    "classify_unmerged_conflicts",
    "declared_decision_paths",
    "escalate_on_sight",
    "gitlink_escalation_message",
    "mechanical_paths",
    "out_of_reach_escalation_message",
    "out_of_reach_paths",
    "out_of_reach_reasons",
    "stage_mechanical_conflicts",
]

_GITLINK_MODE = "160000"
_TREE_MODE_PREFIX = "040000"


def gitlink_escalation_message(paths: Sequence[str]) -> str:
    command = operator_command_for_paths(paths)
    joined = ", ".join(paths)
    if command:
        return f"OUT_OF_REACH: gitlink conflict on {joined} needs operator action: {command}"
    return f"OUT_OF_REACH: gitlink conflict on {joined}"


def out_of_reach_escalation_message(
    paths: Sequence[str],
    reasons: Mapping[str, str] | None = None,
) -> str:
    """Format the escalation message naming unreachable paths and operator command."""
    reasons_map = reasons or {}
    if not any(p in reasons_map for p in paths):
        return gitlink_escalation_message(paths)
    command = operator_command_for_paths(paths)
    items: list[str] = []
    for path in paths:
        reason = reasons_map.get(path)
        if reason:
            items.append(reason)
        else:
            items.append(f"gitlink conflict on {path}")
    joined_reasons = "; ".join(items)
    if command:
        return f"OUT_OF_REACH: {joined_reasons} needs operator action: {command}"
    return f"OUT_OF_REACH: {joined_reasons}"


def escalate_on_sight(
    paths: Sequence[str],
    *,
    session: ResolutionSession | None = None,
    reasons: Mapping[str, str] | None = None,
) -> str:
    paths_tuple = tuple(paths)
    message = out_of_reach_escalation_message(paths_tuple, reasons)
    target_session = session
    if target_session is None:
        try:
            from ralph.pipeline.conflict_resolution.rebase_loop import (
                active_rebase_resolution_session,
            )

            target_session = active_rebase_resolution_session()
        except ImportError:
            target_session = None

    if target_session is not None:
        from ralph.git.resolution_failure_cause import ResolutionFailureCause
        from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
            ResolutionTerminationReason,
        )

        target_session.terminal_reason = ResolutionTerminationReason.OUT_OF_REACH
        target_session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        target_session.unresolved_paths = paths_tuple
        target_session.exhaustion_reason = message
        target_session.operator_action_message = message
    return message


class ConflictSight(StrEnum):
    MECHANICAL = "mechanical"
    AGENT = "agent"
    AGENT_DECISION = "agent_decision"
    OUT_OF_REACH = "out_of_reach"


def out_of_reach_reasons(kinds: dict[str, ConflictSight]) -> dict[str, str]:
    """Return reasons for paths classified as OUT_OF_REACH, if known."""
    if isinstance(kinds, ConflictClassification):
        return dict(kinds.reasons)
    return {}


def out_of_reach_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    return tuple(path for path, kind in kinds.items() if kind is ConflictSight.OUT_OF_REACH)


def declared_decision_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    return tuple(path for path, kind in kinds.items() if kind is ConflictSight.AGENT_DECISION)


def mechanical_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    return tuple(path for path, kind in kinds.items() if kind is ConflictSight.MECHANICAL)


def classify_stage_map(
    stages: dict[int, tuple[str, str]],
    *,
    binary: bool,
    has_markers: bool = True,
) -> ConflictSight:
    if binary:
        return ConflictSight.AGENT_DECISION
    ours = stages.get(2)
    theirs = stages.get(3)
    if ours is None and theirs is None:
        return ConflictSight.AGENT
    if ours is None or theirs is None:
        return _classify_one_sided(ours if ours is not None else theirs)
    ours_mode, ours_blob = ours
    theirs_mode, theirs_blob = theirs
    if ours_mode in {_GITLINK_MODE} or theirs_mode in {_GITLINK_MODE}:
        return _classify_gitlink(ours_mode, ours_blob, theirs_mode, theirs_blob, stages.get(1))
    if _is_tree(ours_mode) != _is_tree(theirs_mode):
        return ConflictSight.AGENT_DECISION
    return _classify_two_sided_blob(ours_blob, theirs_blob, has_markers=has_markers)


def _classify_two_sided_blob(
    ours_blob: str, theirs_blob: str, *, has_markers: bool
) -> ConflictSight:
    if ours_blob == theirs_blob:
        return ConflictSight.MECHANICAL
    return ConflictSight.AGENT if has_markers else ConflictSight.AGENT_DECISION


def _classify_one_sided(present: tuple[str, str] | None) -> ConflictSight:
    if present is None:
        return ConflictSight.OUT_OF_REACH
    mode, _blob = present
    if mode == _GITLINK_MODE:
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


def classify_unmerged_conflicts(root: Path, paths: tuple[str, ...]) -> ConflictClassification:
    entries = conflict_stage_entries(root, paths)
    marked = set(paths_with_conflict_markers(root, paths))
    kinds: dict[str, ConflictSight] = {}
    reasons: dict[str, str] = {}
    entry_failures: dict[str, str] = (
        dict(entries.failures) if isinstance(entries, ConflictStageEntries) else {}
    )
    for path in paths:
        if path in entry_failures:
            kinds[path] = ConflictSight.OUT_OF_REACH
            reasons[path] = entry_failures[path]
        else:
            kinds[path] = classify_stage_map(
                entries.get(path, {}),
                binary=_path_is_binary(root, path),
                has_markers=path in marked,
            )
    return ConflictClassification(kinds, reasons=reasons)


def stage_mechanical_conflicts(root: Path, kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
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
