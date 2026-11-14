from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import conflict_stage_entries
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline.conflict_resolution.gitlink_policy import (
    GitlinkReviewRecord,
    choose_gitlink,
)
from ralph.pipeline.conflict_resolution.sight import ConflictKind

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from ralph.config.gitlink_policy_config import GitlinkPolicyConfig
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop

__all__ = [
    "resolve_completion_in_place",
    "try_deterministic_resolution",
]

_CONFLICT_STAGE_OURS = 2
_CONFLICT_STAGE_THEIRS = 3


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


def try_deterministic_resolution(
    root: Path,
    stop: RebaseStop,
    gitlink_policy: GitlinkPolicyConfig | None = None,
) -> bool:
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
            return _resolve_gitlinks(root, stop.conflicted_files, stages, gitlink_policy)
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
