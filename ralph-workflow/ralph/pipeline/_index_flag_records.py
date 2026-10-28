"""Index-flag probe records for the single worktree classifier.

``git update-index --assume-unchanged`` and ``git update-index
--skip-worktree`` hide a path from ``git status --porcelain``, from
``git diff HEAD`` and from ``git stash create`` — the three seams a
porcelain-only worktree observation trusts — so a flagged-only dirty
tree classifies as CLEAN and its content is never preserved. This
module is the bounded, typed probe that closes the blind spot:
:func:`read_index_flag_dirt` reads the flags (``git ls-files -v -z``)
and the real content (``git hash-object`` vs ``git ls-tree HEAD``) and
collapses every failure mode to :meth:`Reading.unknown`, never to a
confirmed-clean reading.

The :class:`~ralph.pipeline.integration_observation.Reading` import is
deferred to call time so this private module and the public observation
module never import each other at module load (the same discipline
:mod:`ralph.pipeline._porcelain_records` follows).
"""

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
        """The git-runner seam the probe executes through.

        The default is the serialized, hardened production runner; the
        observation module passes its own module binding so a test that
        substitutes the observation seam's ``run_git`` intercepts the
        flag probe exactly like the other probes.
        """

        def __call__(
            self,
            args: Sequence[str],
            *,
            cwd: Path | None,
            label: str,
            options: GitRunOptions | None = None,
            call_class: GitCallClass | None = None,
        ) -> GitRunResult: ...


#: ``git ls-files -v`` tag marking an assume-unchanged path (lowercase).
_LS_FILES_TAG_ASSUME_UNCHANGED: Final[str] = "h"

#: ``git ls-files -v`` tag marking a skip-worktree path.
_LS_FILES_TAG_SKIP_WORKTREE: Final[str] = "S"

#: All ``git ls-files -v`` tags this probe acts on.
_LS_FILES_FLAG_TAGS: Final[frozenset[str]] = frozenset(
    {_LS_FILES_TAG_ASSUME_UNCHANGED, _LS_FILES_TAG_SKIP_WORKTREE}
)

#: Width of one well-formed ``git ls-files -v -z`` record: the tag, the
#: separator space, at least one path character.
_LS_FILES_RECORD_MIN_WIDTH: Final[int] = 3

#: Field count of one ``git ls-tree`` meta column run: mode, type, oid.
_LS_TREE_META_FIELDS: Final[int] = 3


@dataclass(frozen=True, slots=True)
class IndexFlagDirt:
    """Typed reading of the index flags that hide tracked dirt from porcelain.

    ``git update-index --assume-unchanged`` and
    ``git update-index --skip-worktree`` both hide a path from
    ``git status --porcelain``, from ``git diff HEAD`` and from
    ``git stash create`` — the seams a porcelain-only observation
    trusts — while ``git ls-files -v`` still
    names the flagged paths (lowercase ``h`` for assume-unchanged,
    ``S`` for skip-worktree) and ``git hash-object`` still reads their
    real worktree content. This reading is the probe's typed answer:
    the two flag sets, the flagged paths whose content differs from
    HEAD (``changed_paths`` — the hidden dirt), and a sorted
    ``(path, worktree content hash)`` pair per flagged path so a caller
    with two readings (baseline vs after) can diff exactly which
    flagged content moved. All collections are immutable; an empty
    reading means the probe ran and proved no flag is set.
    """

    assume_unchanged: frozenset[str]
    skip_worktree: frozenset[str]
    changed_paths: frozenset[str]
    path_hashes: tuple[tuple[str, str], ...] = ()

    @classmethod
    def empty(cls) -> IndexFlagDirt:
        """The confirmed no-flags reading."""
        return cls(frozenset(), frozenset(), frozenset(), ())


def _unreadable(what: str, detail: str) -> Reading[str]:
    """Build the canonical unknown reading for a failed git probe."""
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
    """Run one flag-probe git call through ``git_runner`, lifting every failure to unknown."""
    from ralph.pipeline.integration_observation import Reading

    try:
        result = git_runner(args, cwd=root, label=label)
    except Exception as exc:
        return _unreadable(what, str(exc) or type(exc).__name__)
    if result.returncode != 0:
        return _unreadable(what, f"exit={result.returncode}")
    return Reading.known(result.stdout)


def _parse_ls_files_v_z(blob: str) -> dict[str, str]:
    """Map flagged path -> flag tag from one ``git ls-files -v -z`` blob.

    Only the two flag tags carry hidden-dirt evidence and are collected;
    every other record (``H``, ``M``, ``R``, ``C`` …) proves nothing
    about flags and is skipped. Records that are not well-formed
    ``<tag><space><path>`` rows carry no flag evidence either and are
    skipped the same way — the stream's flag content is what it is, and
    a truncated or foreign stream still fails closed at the exit-code
    and line-count checks in :func:`read_index_flag_dirt`.
    """
    flagged: dict[str, str] = {}
    for record in blob.split("\0"):
        if len(record) < _LS_FILES_RECORD_MIN_WIDTH or record[1] != " ":
            continue
        tag = record[0]
        if tag in _LS_FILES_FLAG_TAGS:
            flagged[record[2:]] = tag
    return flagged


def _parse_ls_tree_z(blob: str) -> dict[str, str]:
    """Map path -> blob oid from one ``git ls-tree -z HEAD`` blob."""
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
    """Read the index flags that hide tracked dirt, with content evidence.

    ``git_runner`` overrides the git seam (the observation module passes
    its own ``run_git`` binding so a substituted seam intercepts this
    probe like every other probe); the default is the serialized,
    hardened production runner. Three bounded git calls run through it:

    * ``git ls-files -v -z`` names every assume-unchanged (``h``) and
      skip-worktree (``S``) path. A flag-free index answers here — one
      call, confirmed empty, never unknown.
    * ``git hash-object -- <flagged paths>`` reads the real worktree
      content the flags hide (``hash-object`` ignores both flags).
    * ``git ls-tree -z HEAD -- <flagged paths>`` reads the HEAD blobs
      the content compares against. A flagged path absent from HEAD (or
      whose hash stream is truncated) counts as changed.

    A non-zero exit, a raised error or a hash stream whose line count
    does not match the flagged path count yields
    :meth:`Reading.unknown` — never a confirmed-empty (i.e. "clean")
    reading: an unreadable probe backs off and re-observes.
    """
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
