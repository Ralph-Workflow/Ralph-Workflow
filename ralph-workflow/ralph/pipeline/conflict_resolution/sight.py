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
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.conflict_resolution._conflict_classification import ConflictClassification
from ralph.pipeline.conflict_resolution._kind import ConflictKind
from ralph.pipeline.conflict_resolution._operator_command import operator_command_for_paths

__all__ = [
    "AGENT_ELIGIBLE_SIGHTS",
    "GITLINK_POLICY_REASON_PREFIX",
    "ConflictClassification",
    "ConflictKind",
    "ConflictSight",
    "agent_eligible_paths",
    "classify_conflict_kinds",
    "classify_stage_map",
    "classify_unmerged_conflicts",
    "declared_decision_paths",
    "escalate_on_sight",
    "gitlink_escalation_message",
    "mechanical_paths",
    "out_of_reach_escalation_message",
    "out_of_reach_paths",
    "out_of_reach_reasons",
    "policy_gitlink_paths",
    "stage_mechanical_conflicts",
]

_GITLINK_MODE = "160000"
_TREE_MODE_PREFIX = "040000"
_CONFLICT_STAGE_OURS = 2
_CONFLICT_STAGE_THEIRS = 3

GITLINK_POLICY_REASON_PREFIX = "gitlink pointer policy: "


def gitlink_escalation_message(paths: Sequence[str]) -> str:
    command = operator_command_for_paths(paths)
    joined = ", ".join(paths)
    if command:
        return (
            f"GITLINK_POLICY: the gitlink pointer policy could not complete on {joined}; "
            f"needs operator action: {command}"
        )
    return f"GITLINK_POLICY: the gitlink pointer policy could not complete on {joined}"


def out_of_reach_escalation_message(
    paths: Sequence[str],
    reasons: Mapping[str, str] | None = None,
) -> str:
    reasons_map = reasons or {}
    if not any(p in reasons_map for p in paths):
        return gitlink_escalation_message(paths)
    command = operator_command_for_paths(paths)
    unreachable_items: list[str] = []
    policy_items: list[str] = []
    for path in paths:
        reason = reasons_map.get(path) or ""
        if reason.startswith(GITLINK_POLICY_REASON_PREFIX):
            policy_items.append(reason.removeprefix(GITLINK_POLICY_REASON_PREFIX))
        elif reason:
            unreachable_items.append(reason)
        else:
            unreachable_items.append(f"conflict on {path}")
    parts: list[str] = []
    if unreachable_items:
        parts.append("OUT_OF_REACH: " + "; ".join(unreachable_items))
    if policy_items:
        parts.append("GITLINK_POLICY: " + "; ".join(policy_items))
    joined_reasons = "; ".join(parts)
    if command:
        return f"{joined_reasons} needs operator action: {command}"
    return joined_reasons


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
    GITLINK_POLICY = "gitlink_policy"


AGENT_ELIGIBLE_SIGHTS: frozenset[ConflictSight] = frozenset(
    {ConflictSight.AGENT, ConflictSight.AGENT_DECISION}
)


def classify_conflict_kinds(
    root: Path,
    paths: tuple[str, ...],
    entries: ConflictStageEntries | None = None,
) -> dict[str, ConflictKind]:
    stage_entries = conflict_stage_entries(root, paths) if entries is None else entries
    append_only = _append_only_paths(root, paths)
    kinds: dict[str, ConflictKind] = {}
    for path in paths:
        stages = stage_entries.get(path, {})
        modes = {mode for mode, _sha in stages.values()}
        if "160000" in modes:
            kinds[path] = ConflictKind.GITLINK
        elif path in append_only:
            kinds[path] = ConflictKind.APPEND_ONLY
        elif not stages or (
            _CONFLICT_STAGE_OURS not in stages and _CONFLICT_STAGE_THEIRS not in stages
        ):
            kinds[path] = ConflictKind.TEXT
        elif (_CONFLICT_STAGE_OURS in stages) != (_CONFLICT_STAGE_THEIRS in stages):
            kinds[path] = ConflictKind.DELETE_MODIFY
        elif _path_is_binary(root, path):
            kinds[path] = ConflictKind.BINARY
        elif stages[_CONFLICT_STAGE_OURS][0] != stages[_CONFLICT_STAGE_THEIRS][0]:
            kinds[path] = ConflictKind.DECLARED_DECISION
        else:
            kinds[path] = ConflictKind.TEXT
    return kinds


def out_of_reach_reasons(kinds: dict[str, ConflictSight]) -> dict[str, str]:
    if isinstance(kinds, ConflictClassification):
        return dict(kinds.reasons)
    return {}


def out_of_reach_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    return tuple(
        path
        for path, kind in kinds.items()
        if kind in {ConflictSight.OUT_OF_REACH, ConflictSight.GITLINK_POLICY}
    )


def policy_gitlink_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    return tuple(path for path, kind in kinds.items() if kind is ConflictSight.GITLINK_POLICY)


def agent_eligible_paths(kinds: dict[str, ConflictSight]) -> tuple[str, ...]:
    return tuple(path for path, kind in kinds.items() if kind in AGENT_ELIGIBLE_SIGHTS)


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
    ours = stages.get(2)
    theirs = stages.get(3)
    if ours is None and theirs is None:
        return ConflictSight.AGENT_DECISION if binary else ConflictSight.AGENT
    gitlink_sight = ConflictSight.GITLINK_POLICY
    if ours is None or theirs is None:
        present = ours if ours is not None else theirs
        if present is not None and present[0] == _GITLINK_MODE:
            return gitlink_sight
        return (
            ConflictSight.AGENT_DECISION if binary else _classify_one_sided(present, gitlink_sight)
        )

    ours_mode, ours_blob = ours
    theirs_mode, theirs_blob = theirs
    if ours_mode in {_GITLINK_MODE} or theirs_mode in {_GITLINK_MODE}:
        return _classify_gitlink(
            ours_mode,
            ours_blob,
            theirs_mode,
            theirs_blob,
            stages.get(1),
            gitlink_sight=gitlink_sight,
        )
    if binary or (_is_tree(ours_mode) != _is_tree(theirs_mode)):
        return ConflictSight.AGENT_DECISION
    return _classify_two_sided_blob(ours_blob, theirs_blob, has_markers=has_markers)


def _classify_two_sided_blob(
    ours_blob: str, theirs_blob: str, *, has_markers: bool
) -> ConflictSight:
    if ours_blob == theirs_blob:
        return ConflictSight.MECHANICAL
    return ConflictSight.AGENT if has_markers else ConflictSight.AGENT_DECISION


def _classify_one_sided(
    present: tuple[str, str] | None,
    gitlink_sight: ConflictSight = ConflictSight.GITLINK_POLICY,
) -> ConflictSight:
    if present is None:
        return ConflictSight.OUT_OF_REACH
    mode, _blob = present
    if mode == _GITLINK_MODE:
        return gitlink_sight
    return ConflictSight.AGENT_DECISION


def _classify_gitlink(
    ours_mode: str,
    ours_blob: str,
    theirs_mode: str,
    theirs_blob: str,
    ancestor: tuple[str, str] | None,
    gitlink_sight: ConflictSight = ConflictSight.GITLINK_POLICY,
) -> ConflictSight:
    if ours_mode == theirs_mode == _GITLINK_MODE and ancestor is not None:
        _ancestor_mode, ancestor_blob = ancestor
        if ancestor_blob == ours_blob == theirs_blob:
            return ConflictSight.MECHANICAL
    return gitlink_sight


def classify_unmerged_conflicts(root: Path, paths: tuple[str, ...]) -> ConflictClassification:
    entries = conflict_stage_entries(root, paths)
    typed_kinds = classify_conflict_kinds(root, paths, entries)
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
        elif typed_kinds[path] is ConflictKind.GITLINK:
            kinds[path] = ConflictSight.GITLINK_POLICY
            reasons[path] = f"{GITLINK_POLICY_REASON_PREFIX}conflict on {path}"
        elif typed_kinds[path] in {ConflictKind.BINARY, ConflictKind.OTHER}:
            kinds[path] = ConflictSight.OUT_OF_REACH
            reasons[path] = (
                f"{typed_kinds[path].value} conflict on {path} requires pipeline completion-in-place"
            )
        elif typed_kinds[path] is ConflictKind.APPEND_ONLY:
            kinds[path] = ConflictSight.MECHANICAL
        elif typed_kinds[path] is ConflictKind.DELETE_MODIFY:
            kinds[path] = ConflictSight.AGENT_DECISION
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


def _append_only_paths(root: Path, paths: tuple[str, ...]) -> set[str]:
    if not paths:
        return set()
    result = run_git(
        ("check-attr", "-z", "merge", "--", *paths),
        cwd=root,
        label="git-append-only-attributes",
    )
    if result.returncode != 0:
        return set()
    fields = result.stdout.split("\0")
    return {fields[index] for index in range(0, len(fields) - 2, 3) if fields[index + 2] == "union"}
