"""Keep a resolver inside the paths it was handed, and undo what it was not.

The conflict prompt forbids editing any path that is not conflicted.
This module is the enforcement that makes the prohibition real rather
than advisory: it reads what the worktree actually changed, subtracts
the conflicted paths and Ralph's own bookkeeping, and then puts back
whatever is left over -- restoring a tracked file, and moving an
untracked one aside rather than destroying it.

Split out of :mod:`ralph.pipeline.conflict_resolution.rebase_loop`,
which keeps the per-stop gate itself (it needs the stop) and calls
these primitives through its own module globals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "is_ralph_workspace_path",
    "move_stray_aside",
    "restore_one_unrequested_path",
    "revert_unrequested_paths",
    "worktree_dirty_paths",
]


def worktree_dirty_paths(root: Path) -> frozenset[str] | None:
    """Tracked paths whose worktree content differs from the index.

    During a paused rebase this set is exactly the conflicted paths: the
    replayed commit's non-conflicting changes are already staged, so they
    match the worktree and do not appear. Anything ELSE in the set after
    a resolver has run is a file the resolver edited without being asked
    to.

    Returns ``None`` when git could not answer. The caller must treat
    that as a rejection rather than as "nothing changed": an unreadable
    worktree is precisely the state in which an unnoticed edit would be
    replayed into the commit.
    """
    # ``git diff`` lists tracked MODIFICATIONS only, so a file the
    # resolver CREATED was invisible to the out-of-scope guard: never
    # reported, never reverted, and then swept up by a later `git add`.
    # Porcelain sees created and untracked paths too, and ``-z`` keeps a
    # non-ASCII name from arriving quoted and unopenable.
    result = run_git(
        ("status", "--porcelain=v1", "-z"),
        cwd=root,
        label="git-worktree-dirty-paths",
    )
    if result.returncode != 0:
        logger.warning(
            "conflict_resolution: could not read the worktree status: {}",
            result.stderr.strip(),
        )
        return None
    return frozenset(_porcelain_paths(result.stdout))


#: A porcelain v1 entry is a two-letter status code, a space, then the path,
#: so anything shorter carries no path to report.
_PORCELAIN_ENTRY_MIN_LEN: Final[int] = 4


def _porcelain_paths(blob: str) -> list[str]:
    """Paths from a ``--porcelain=v1 -z`` blob, renames included."""
    entries = [entry for entry in blob.split("\0") if entry]
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < _PORCELAIN_ENTRY_MIN_LEN:
            continue
        code, path = entry[:2], entry[3:]
        paths.append(path)
        # A rename/copy is followed by its source path.
        if ("R" in code or "C" in code) and index < len(entries):
            paths.append(entries[index])
            index += 1
    return paths


#: Ralph's own workspace directory, written DURING the resolution it is
#: judging: the prompt is rendered to `.agent/tmp/`, and artifacts,
#: transcripts and progress records land there too. Charging those to
#: the resolver rejected the resolution -- the agent never touched them,
#: and the run then abandoned a rebase it had actually resolved.
_RALPH_WORKSPACE_PREFIX = ".agent/"


def is_ralph_workspace_path(path: str) -> bool:
    """Whether ``path`` is Ralph's own bookkeeping rather than the agent's."""
    # `lstrip("./")` would strip the leading dot itself, turning
    # ".agent/tmp/x" into "agent/tmp/x" and matching nothing.
    normalised = path.strip().removeprefix("./")
    return normalised == ".agent" or normalised.startswith(_RALPH_WORKSPACE_PREFIX)


def restore_one_unrequested_path(root: Path, path: str) -> bool:
    """Undo one stray edit: restore a tracked path, delete an untracked one.

    Reverting the batch in a single ``git checkout`` failed whenever ONE
    of the strays was a file the resolver created -- and the fallback
    then unlinked every path in the batch, tracked ones included, which
    turned an out-of-scope edit into an out-of-scope deletion.
    """
    tracked = run_git(
        ("ls-files", "--error-unmatch", "--", path),
        cwd=root,
        label="git-stray-tracked",
    )
    if tracked.returncode == 0:
        restored = run_git(
            ("checkout", "--", path),
            cwd=root,
            label="git-revert-stray",
        )
        return restored.returncode == 0
    target = root / path
    if target.is_dir():
        # An untracked directory is not an edit to anything git tracks,
        # and refusing here threw away a resolution that had already
        # been proven -- one `__pycache__/` was enough, every run.
        logger.info(
            "conflict_resolution: leaving untracked directory '{}' in place", path
        )
        return True
    return move_stray_aside(target)


def move_stray_aside(target: Path) -> bool:
    """Take an untracked stray out of the way WITHOUT destroying it.

    These paths are only inferred to be the resolver's: anything that
    appeared during the session looks the same, including an operator's
    own file in a shared checkout. Unlinking made that guess
    unrecoverable, so the file is renamed instead and the operator is
    told where it went.
    """
    if not target.exists() and not target.is_symlink():
        return True
    for suffix in range(1, 100):
        aside = target.with_name(f"{target.name}.ralph-set-aside-{suffix}")
        if aside.exists():
            continue
        try:
            target.rename(aside)
        except OSError:
            return False
        logger.warning(
            "conflict_resolution: '{}' was not part of the conflict; moved it aside to '{}'",
            target.name,
            aside.name,
        )
        return True
    return False


def revert_unrequested_paths(root: Path, paths: tuple[str, ...]) -> bool:
    """Drop resolver edits that were outside the conflicted paths."""
    if not paths:
        return True
    checkout = run_git(
        ("checkout", "--", *paths),
        cwd=root,
        label="git-revert-unrequested-paths",
    )
    if checkout.returncode == 0:
        return True
    # The batch fails as a whole if ANY stray is a file the resolver
    # created, so each path is undone on its own terms: a tracked file
    # is restored, an untracked one is removed. Unlinking the whole
    # batch deleted tracked files the resolver had merely edited.
    return all(restore_one_unrequested_path(root, path) for path in paths)
