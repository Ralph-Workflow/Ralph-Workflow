from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

from loguru import logger

from ralph.git.merge import (
    ConflictStageEntries,
    conflict_stage_entries,
    paths_with_conflict_markers,
    stage_paths,
    unmerged_paths,
)
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline.conflict_resolution.gitlink_policy import (
    GitlinkReviewRecord,
    choose_gitlink,
)
from ralph.pipeline.conflict_resolution.sight import (
    ConflictKind,
    ConflictSight,
    classify_conflict_kinds,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.gitlink_policy_config import GitlinkPolicyConfig
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop


@runtime_checkable
class _StopWithConflicts(Protocol):
    @property
    def conflicted_files(self) -> tuple[str, ...]: ...


__all__ = [
    "MixedKindResolution",
    "resolve_completion_in_place",
    "resolve_mixed_kind_deterministic",
    "try_deterministic_resolution",
    "try_mixed_kind_resolution",
]

_CONFLICT_STAGE_OURS = 2
_CONFLICT_STAGE_THEIRS = 3


class MixedKindResolution(NamedTuple):
    resolved_gitlink_paths: tuple[str, ...]
    remaining_text_paths: tuple[str, ...]

    @property
    def resolved_paths(self) -> tuple[str, ...]:
        return self.resolved_gitlink_paths

    @property
    def remaining_paths(self) -> tuple[str, ...]:
        return self.remaining_text_paths


def _is_gitlink_kind(kind: object) -> bool:
    if kind is None:
        return False
    return (
        kind is ConflictKind.GITLINK
        or kind is ConflictSight.GITLINK_POLICY
        or (isinstance(kind, str) and kind.lower() in {"gitlink", "gitlink_policy"})
    )


def _is_text_kind(kind: object) -> bool:
    if kind is None:
        return True
    return (
        kind is ConflictKind.TEXT
        or kind in {ConflictSight.AGENT, ConflictSight.AGENT_DECISION}
        or (isinstance(kind, str) and kind.lower() in {"text", "agent", "agent_decision"})
    )


def _extract_kinds(
    root: Path,
    paths: tuple[str, ...],
    kinds: object,
) -> Mapping[str, object]:
    if isinstance(kinds, Mapping):
        return kinds
    return classify_conflict_kinds(root, paths)


def _pick_and_stage_gitlinks(
    root: Path,
    gitlinks: list[str],
    policy: GitlinkPolicyConfig | None,
) -> tuple[str, ...]:
    entries = conflict_stage_entries(root, tuple(gitlinks))
    chosen: list[tuple[str, str, str]] = []
    for path in gitlinks:
        stage = entries.get(path, {})
        picked = _gitlink_completion_choice(stage, path, policy)
        if picked is not None:
            chosen.append((picked[0], picked[1], path))
    if not chosen:
        return ()
    restore_lines = [
        f"{m} {h} {stg_num}\t{p}\n"
        for p in gitlinks
        for stg_num, (m, h) in entries.get(p, {}).items()
    ]
    if not _stage_deterministic_entries(
        root,
        chosen,
        label="git-mixed-kind-gitlink",
        restore_info="".join(restore_lines),
    ):
        return ()
    return tuple(path for _mode, _sha, path in chosen)


def resolve_mixed_kind_deterministic(
    root: Path,
    paths: tuple[str, ...] | _StopWithConflicts | Iterable[str],
    kinds: Mapping[str, object] | None = None,
    gitlink_policy: GitlinkPolicyConfig | None = None,
) -> MixedKindResolution:
    target_paths: tuple[str, ...]
    match paths:
        case tuple():
            target_paths = paths
        case _StopWithConflicts():
            target_paths = paths.conflicted_files
        case _:
            target_paths = tuple(paths)
    if not target_paths:
        return MixedKindResolution((), ())

    effective_kinds = _extract_kinds(root, target_paths, kinds)
    gitlinks = [p for p in target_paths if _is_gitlink_kind(effective_kinds.get(p))]
    if not gitlinks:
        remaining = tuple(p for p in target_paths if _is_text_kind(effective_kinds.get(p)))
        return MixedKindResolution((), remaining or target_paths)

    staged = _pick_and_stage_gitlinks(root, gitlinks, gitlink_policy)
    remaining = tuple(p for p in target_paths if p not in staged)
    return MixedKindResolution(staged, remaining)


try_mixed_kind_resolution = resolve_mixed_kind_deterministic


def resolve_completion_in_place(
    root: Path,
    paths: tuple[str, ...],
    kinds: dict[str, ConflictKind],
    gitlink_policy: GitlinkPolicyConfig | None = None,
) -> tuple[str, ...]:
    selected = tuple(
        path
        for path in paths
        if kinds.get(path) in {ConflictKind.GITLINK, ConflictKind.BINARY, ConflictKind.OTHER}
    )
    if not selected:
        return ()
    entries = conflict_stage_entries(root, selected)
    chosen: list[tuple[str, str, str]] = []
    for path in selected:
        stage = entries.get(path, {})
        picked = (
            _gitlink_completion_choice(stage, path, gitlink_policy)
            if kinds.get(path) is ConflictKind.GITLINK
            else _blob_completion_choice(stage, path)
        )
        if picked is None:
            continue
        mode, sha = picked
        chosen.append((mode, sha, path))
    if len(chosen) != len(selected):
        return ()
    if not _stage_deterministic_entries(
        root,
        chosen,
        label="git-completion-in-place",
    ):
        return ()
    return tuple(path for _mode, _sha, path in chosen)


def _stage_shas(stage: dict[int, tuple[str, str]]) -> tuple[str, str, str]:
    ancestor = stage.get(1)
    ours = stage.get(_CONFLICT_STAGE_OURS)
    theirs = stage.get(_CONFLICT_STAGE_THEIRS)
    return (
        ancestor[1] if ancestor is not None else "<absent>",
        ours[1] if ours is not None else "<absent>",
        theirs[1] if theirs is not None else "<absent>",
    )


def _blob_completion_choice(
    stage: dict[int, tuple[str, str]],
    path: str,
) -> tuple[str, str] | None:
    picked = stage.get(_CONFLICT_STAGE_OURS) or stage.get(_CONFLICT_STAGE_THEIRS)
    if picked is None:
        return None
    mode, sha = picked
    base, ours, theirs = _stage_shas(stage)
    logger.info(
        "conflict_resolution: completion-in-place {} base={} ours={} theirs={} selected={}",
        path,
        base,
        ours,
        theirs,
        sha,
    )
    return (mode, sha)


def _gitlink_completion_choice(
    stage: dict[int, tuple[str, str]],
    path: str,
    policy: GitlinkPolicyConfig | None,
) -> tuple[str, str] | None:
    base, ours_sha, theirs_sha = _stage_shas(stage)
    record = choose_gitlink(path, base, ours_sha, theirs_sha, policy)
    ours = stage.get(_CONFLICT_STAGE_OURS)
    theirs = stage.get(_CONFLICT_STAGE_THEIRS)
    if ours is None:
        if theirs is None:
            return None
        record = replace(record, selected_side="theirs", selected_sha=theirs[1])
        _log_gitlink_review(record)
        return (theirs[0], theirs[1])
    if theirs is None:
        record = replace(record, selected_side="ours", selected_sha=ours[1])
        _log_gitlink_review(record)
        return (ours[0], ours[1])
    _log_gitlink_review(record)
    return (ours[0], record.selected_sha)


def _log_gitlink_review(record: GitlinkReviewRecord) -> None:
    logger.info(
        "conflict_resolution: gitlink policy path={} policy={} base={} ours={} "
        "theirs={} selected_side={} selected={} alternatives={}",
        record.path,
        record.policy,
        record.base,
        record.ours,
        record.theirs,
        record.selected_side,
        record.selected_sha,
        record.alternatives_considered,
    )


_UNMERGED_QUERY_FAILED = "<unmerged-path-query-failed>"


def _partition_deterministic_conflicts(
    root: Path,
    conflicted: tuple[str, ...],
    entries: ConflictStageEntries,
) -> tuple[list[str], list[str], list[dict[int, tuple[str, str]]], list[str]] | None:
    kinds = classify_conflict_kinds(root, conflicted, entries)
    gitlinks: list[str] = []
    mode_only_paths: list[str] = []
    mode_only_stages: list[dict[int, tuple[str, str]]] = []
    text_paths: list[str] = []

    for path in conflicted:
        stage = entries.get(path, {})
        modes = {m for m, _h in stage.values()}
        if "160000" in modes:
            gitlinks.append(path)
        elif (
            _CONFLICT_STAGE_OURS in stage
            and _CONFLICT_STAGE_THEIRS in stage
            and stage[_CONFLICT_STAGE_OURS][1] == stage[_CONFLICT_STAGE_THEIRS][1]
            and {stage[_CONFLICT_STAGE_OURS][0], stage[_CONFLICT_STAGE_THEIRS][0]}
            == {"100644", "100755"}
        ):
            mode_only_paths.append(path)
            mode_only_stages.append(stage)
        elif kinds.get(path) is ConflictKind.TEXT:
            text_paths.append(path)
        else:
            return None
    if not gitlinks and not mode_only_paths:
        return None
    return gitlinks, mode_only_paths, mode_only_stages, text_paths


def _stage_resolved_text_paths(root: Path, text_paths: list[str]) -> bool:
    stageable = [
        p
        for p in text_paths
        if (root / p).is_file()
        and (root / p).stat().st_size > 0
        and not paths_with_conflict_markers(root, (p,))
    ]
    if len(stageable) != len(text_paths):
        return False
    stage_paths(root, tuple(stageable))
    return True


def _execute_deterministic_resolution(
    root: Path,
    stop: RebaseStop,
    entries: ConflictStageEntries,
    partition: tuple[list[str], list[str], list[dict[int, tuple[str, str]]], list[str]],
    gitlink_policy: GitlinkPolicyConfig | None,
) -> bool:
    gitlinks, mode_only_paths, mode_only_stages, text_paths = partition

    if gitlinks and not mode_only_paths and not text_paths:
        stages = [entries[p] for p in gitlinks]
        return _resolve_gitlinks(root, tuple(gitlinks), stages, gitlink_policy)

    if mode_only_paths and not gitlinks and not text_paths:
        return _resolve_mode_only(root, tuple(mode_only_paths), mode_only_stages)

    if not _resolve_mixed_deterministic_conflicts(root, partition, gitlink_policy):
        return False

    remaining = unmerged_paths(root)
    return _UNMERGED_QUERY_FAILED not in remaining and not any(
        p in remaining for p in stop.conflicted_files
    )


def _resolve_mixed_deterministic_conflicts(
    root: Path,
    partition: tuple[list[str], list[str], list[dict[int, tuple[str, str]]], list[str]],
    gitlink_policy: GitlinkPolicyConfig | None,
) -> bool:
    gitlinks, mode_only_paths, mode_only_stages, text_paths = partition

    if gitlinks and len(_pick_and_stage_gitlinks(root, gitlinks, gitlink_policy)) != len(gitlinks):
        return False

    if mode_only_paths and not _resolve_mode_only(root, tuple(mode_only_paths), mode_only_stages):
        return False

    return not text_paths or _stage_resolved_text_paths(root, text_paths)


def try_deterministic_resolution(
    root: Path,
    stop: RebaseStop,
    gitlink_policy: GitlinkPolicyConfig | None = None,
) -> bool:
    try:
        entries = conflict_stage_entries(root, stop.conflicted_files)
        partition = _partition_deterministic_conflicts(root, stop.conflicted_files, entries)
        if partition is None:
            return False
        return _execute_deterministic_resolution(root, stop, entries, partition, gitlink_policy)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("conflict_resolution: deterministic resolution declined: {}", exc)
        return False


def _resolve_gitlinks(
    root: Path,
    paths: tuple[str, ...],
    stages: list[dict[int, tuple[str, str]]],
    policy: GitlinkPolicyConfig | None,
) -> bool:
    chosen: list[tuple[str, str]] = []
    for path, stage in zip(paths, stages, strict=True):
        base = stage.get(1, ("160000", "<absent>"))[1]
        ours = stage[_CONFLICT_STAGE_OURS][1]
        theirs = stage[_CONFLICT_STAGE_THEIRS][1]
        record = choose_gitlink(path, base, ours, theirs, policy)
        _log_gitlink_review(record)
        chosen.append((path, record.selected_sha))

    restore_lines: list[str] = []
    for p, stg in zip(paths, stages, strict=True):
        for stg_num, (m, h) in stg.items():
            restore_lines.append(f"{m} {h} {stg_num}\t{p}\n")
    restore_info = "".join(restore_lines)

    return _stage_deterministic_entries(
        root,
        (("160000", sha, path) for path, sha in chosen),
        label="git-gitlink-resolve",
        restore_info=restore_info,
    )


def _resolve_mode_only(
    root: Path,
    paths: tuple[str, ...],
    stages: list[dict[int, tuple[str, str]]],
) -> bool:
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
    restore_info: str | None = None,
) -> bool:
    entries_list = list(entries)
    if not entries_list:
        return True

    args: list[str] = ["update-index"]
    for mode, blob, path in entries_list:
        args.extend(("--cacheinfo", f"{mode},{blob},{path}"))
    rc = run_git(tuple(args), cwd=root, label=label).returncode
    if rc != 0:
        return False

    gitlinks = [(mode, blob, path) for mode, blob, path in entries_list if mode == "160000"]
    if gitlinks:
        for mode, blob, path in gitlinks:
            rc_clear = run_git(
                ("update-index", "--force-remove", path),
                cwd=root,
                label=f"{label}-clear",
            ).returncode
            if rc_clear != 0:
                if restore_info:
                    run_git(
                        ("update-index", "--index-info"),
                        cwd=root,
                        label=f"{label}-restore",
                        options=GitRunOptions(input_data=restore_info),
                    )
                return False

            rc_add = run_git(
                ("update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}"),
                cwd=root,
                label=f"{label}-readd",
            ).returncode
            if rc_add != 0:
                if restore_info:
                    run_git(
                        ("update-index", "--index-info"),
                        cwd=root,
                        label=f"{label}-restore",
                        options=GitRunOptions(input_data=restore_info),
                    )
                return False

    return True
