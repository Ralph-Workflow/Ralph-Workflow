from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from typing import Protocol

    from ralph.git.git_call_policy import GitCallClass
    from ralph.git.git_run_result import GitRunResult
    from ralph.git.subprocess_runner import GitRunOptions
    from ralph.pipeline.integration_observation import Reading

    class _GitRunner(Protocol):
        def __call__(
            self,
            args: Sequence[str],
            *,
            cwd: Path | None,
            label: str,
            options: GitRunOptions | None = None,
            call_class: GitCallClass | None = None,
        ) -> GitRunResult: ...


_LS_FILES_TAG_ASSUME_UNCHANGED: Final[str] = "h"

_LS_FILES_TAG_SKIP_WORKTREE: Final[str] = "S"

_LS_FILES_FLAG_TAGS: Final[frozenset[str]] = frozenset(
    {_LS_FILES_TAG_ASSUME_UNCHANGED, _LS_FILES_TAG_SKIP_WORKTREE}
)

_LS_FILES_RECORD_MIN_WIDTH: Final[int] = 3

_LS_TREE_META_FIELDS: Final[int] = 3


@dataclass(frozen=True, slots=True)
class IndexFlagDirt:
    assume_unchanged: frozenset[str]
    skip_worktree: frozenset[str]
    changed_paths: frozenset[str]
    path_hashes: tuple[tuple[str, str], ...] = ()

    @classmethod
    def empty(cls) -> IndexFlagDirt:
        return cls(frozenset(), frozenset(), frozenset(), ())


def _unreadable(what: str, detail: str) -> Reading[str]:
    from ralph.pipeline.integration_observation import Reading

    return Reading[str].unknown(f"{what} unreadable: {detail}")


def _flag_probe_output(
    args: tuple[str, ...],
    root: Path,
    *,
    label: str,
    what: str,
    git_runner: _GitRunner,
) -> Reading[str]:
    from ralph.pipeline.integration_observation import Reading

    try:
        result = git_runner(args, cwd=root, label=label)
    except Exception as exc:
        return _unreadable(what, str(exc) or type(exc).__name__)
    if result.returncode != 0:
        return _unreadable(what, f"exit={result.returncode}")
    return Reading.known(result.stdout)


def _parse_ls_files_v_z(blob: str) -> dict[str, str]:
    flagged: dict[str, str] = {}
    for record in blob.split("\0"):
        if len(record) < _LS_FILES_RECORD_MIN_WIDTH or record[1] != " ":
            continue
        tag = record[0]
        if tag in _LS_FILES_FLAG_TAGS:
            flagged[record[2:]] = tag
    return flagged


def _parse_ls_tree_z(blob: str) -> dict[str, str]:
    head_oids: dict[str, str] = {}
    for record in blob.split("\0"):
        meta, separator, path = record.partition("\t")
        if not separator:
            continue
        fields = meta.split(" ")
        if len(fields) >= _LS_TREE_META_FIELDS and fields[1] == "blob":
            head_oids[path] = fields[2]
    return head_oids


def read_index_flag_dirt(
    root: Path,
    *,
    git_runner: _GitRunner | None = None,
) -> Reading[IndexFlagDirt]:
    from ralph.pipeline.integration_observation import Reading

    runner = run_git if git_runner is None else git_runner
    listed = _flag_probe_output(
        (*COMMIT_PIN_CONFIG_ARGS, "ls-files", "-v", "-z"),
        root,
        label="integration-observation-index-flags",
        what="git ls-files -v",
        git_runner=runner,
    )
    if not listed.is_known_good:
        return Reading[IndexFlagDirt].unknown(listed.reason or "git ls-files -v unreadable")
    flagged = _parse_ls_files_v_z(listed.require_known())
    if not flagged:
        return Reading.known(IndexFlagDirt.empty())
    paths = sorted(flagged)
    hashed = _flag_probe_output(
        ("hash-object", "--", *paths),
        root,
        label="integration-observation-flag-content",
        what="git hash-object",
        git_runner=runner,
    )
    if not hashed.is_known_good:
        return Reading[IndexFlagDirt].unknown(hashed.reason or "git hash-object unreadable")
    worktree_hashes = hashed.require_known().splitlines()
    if len(worktree_hashes) != len(paths):
        return Reading[IndexFlagDirt].unknown("git hash-object unreadable: truncated stream")
    head = _flag_probe_output(
        ("ls-tree", "-z", "HEAD", "--", *paths),
        root,
        label="integration-observation-flag-head",
        what="git ls-tree HEAD",
        git_runner=runner,
    )
    if not head.is_known_good:
        return Reading[IndexFlagDirt].unknown(head.reason or "git ls-tree HEAD unreadable")
    head_oids = _parse_ls_tree_z(head.require_known())
    content_by_path = dict(zip(paths, worktree_hashes, strict=True))
    return Reading.known(
        IndexFlagDirt(
            assume_unchanged=frozenset(
                path for path in paths if flagged[path] == _LS_FILES_TAG_ASSUME_UNCHANGED
            ),
            skip_worktree=frozenset(
                path for path in paths if flagged[path] == _LS_FILES_TAG_SKIP_WORKTREE
            ),
            changed_paths=frozenset(
                path for path in paths if content_by_path[path] != head_oids.get(path)
            ),
            path_hashes=tuple((path, content_by_path[path]) for path in paths),
        )
    )
