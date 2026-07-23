"""Resolve a conflicted rebase in place, one stopped commit at a time.

A rebase is not a single conflict. It replays N commits and can stop on
a conflict at every one of them, so resolving it means a LOOP: resolve
the stop, prove the resolution, ``git rebase --continue``, repeat. The
previous behaviour -- abort on the first stop and fall back to one
endpoint three-way merge -- meant the rebase-conflict resolution phase
was never reachable from a rebase at all, which is the defect this
module exists to close.

The resolver is injected as a plain callable rather than imported, for
the same reason
:data:`ralph.pipeline.auto_integrate_resolve.ConflictResolver` is: it
keeps this module free of the agent session, the display and the policy
bundle, so the loop can be unit-tested with fakes and driven end to end
by a deterministic stub.

Division of labour, mirroring
:func:`ralph.pipeline.auto_integrate_resolve._stage_verify_and_commit`
exactly: the resolver only EDITS FILES. Ralph stages, Ralph re-scans for
surviving conflict markers, Ralph asks git whether any unmerged path
remains, and only Ralph continues the rebase. An agent running under
Ralph's own MCP exec policy is denied every git invocation, so it could
not stage even if it tried.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ralph.git.merge import (
    conflict_stage_entries,
    paths_with_conflict_markers,
    stage_paths,
    unmerged_paths,
)
from ralph.git.rebase.rebase import get_conflicted_files
from ralph.git.rebase.rebase_continuation import (
    ConflictRemainingError,
    NoRebaseInProgressError,
    RebaseContinuationError,
    continue_rebase_at,
    rebase_in_progress_at,
    verify_rebase_completed_at,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.conflict_resolution.graph import (
    MAX_REBASE_CONFLICT_STOPS,
    TERMINAL_ABANDONED,
    TERMINAL_RESOLVED,
    route_after_stop,
)

#: Placeholder subject used when the stopped commit's subject could not
#: be read. The stop is still resolvable -- the conflicted paths are what
#: the agent actually needs -- so an unreadable subject must not abort it.
_UNKNOWN_SUBJECT = "(subject unavailable)"

#: Where git records the commit a paused rebase is replaying onto, newest
#: backend first. ``rebase-merge`` is the default backend; ``rebase-apply``
#: is still produced by ``--apply`` and by older gits.
_REBASE_ONTO_FILES = ("rebase-merge/onto", "rebase-apply/onto")

#: Where git records how far a paused rebase has got, as ``(current,
#: total)`` state-file pairs, newest backend first. The ``rebase-merge``
#: backend counts with ``msgnum``/``end``; the ``rebase-apply`` backend,
#: still produced by ``--apply`` and by older gits, counts with
#: ``next``/``last``. Read for the operator's benefit only -- see
#: :func:`_read_replay_progress`.
_CONFLICT_STAGE_OURS = 2
_CONFLICT_STAGE_THEIRS = 3

_REBASE_PROGRESS_FILES = (
    ("rebase-merge/msgnum", "rebase-merge/end"),
    ("rebase-apply/next", "rebase-apply/last"),
)

__all__ = [
    "RebaseStop",
    "RebaseStopResolver",
    "resolve_rebase_in_progress",
]


@dataclass(frozen=True)
class RebaseStop:
    """One commit a rebase has paused on because replaying it conflicted.

    Carries exactly the context a resolution session is allowed to see:
    which commit is being replayed and which paths conflicted, plus two
    INDEPENDENT counters that are easy to confuse.

    ``stop_index``/``stop_cap`` are the bounded loop's safety counters:
    how many stops this loop has spent out of the fixed
    :data:`~ralph.pipeline.conflict_resolution.graph.MAX_REBASE_CONFLICT_STOPS`
    it is allowed, which is what terminates the loop. They say nothing
    about how long the rebase is.

    ``replay_index``/``replay_total`` are the operator-facing replay
    position: which of the rebase's own commits is being replayed, read
    from git's rebase state by :func:`_read_replay_progress`. They are
    display-only, both ``None`` when that state is unreadable, and must
    never influence loop termination.
    """

    sha: str
    subject: str
    conflicted_files: tuple[str, ...]
    stop_index: int
    stop_cap: int
    replay_index: int | None = None
    replay_total: int | None = None


#: Resolves ONE rebase stop: ``(root, target, stop) -> resolved``. The
#: implementation is expected to edit the conflicted files and nothing
#: else; staging and continuing are Ralph's job, never the resolver's.
#:
#: ``Callable`` is imported at RUNTIME, not under ``TYPE_CHECKING``: a
#: ``type`` alias body is evaluated lazily, and sphinx autodoc forces
#: that evaluation while building the API reference. The same is true of
#: :data:`ralph.pipeline.conflict_resolution.driver.ResolutionInvoker`.
type RebaseStopResolver = Callable[["Path", str, RebaseStop], bool]


def resolve_rebase_in_progress(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
) -> bool:
    """Drive an in-progress rebase to completion through ``resolver``.

    Args:
        root: Repository root holding the paused rebase.
        target: Branch being rebased onto, passed through to the
            resolver as context and used as the completion check's
            fallback when the replay's own base cannot be read.
        resolver: Called once per stop to resolve that stop's conflicts.

    Returns:
        ``True`` only when the rebase finished AND ``HEAD`` is a
        descendant of the commit the replay was landing on. ``False``
        for every other outcome, including a declining resolver, a
        surviving conflict marker, a refused continuation and an
        exhausted stop budget. A ``target`` a sibling moved during the
        resolution is NOT one of those outcomes -- see
        :func:`_resolve_stops`.

    Never raises. The caller treats ``False`` as "resolution declined"
    and falls through to the pre-existing abort-then-endpoint-merge
    path, so an unexpected failure here degrades to exactly the
    behaviour that shipped before this module existed.
    """
    try:
        return _resolve_stops(root, target, resolver)
    except Exception as exc:
        logger.warning(
            "conflict_resolution: rebase resolution loop failed for '{}': {}",
            target,
            exc,
        )
        return False


def _resolve_stops(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
) -> bool:
    """Body of the bounded loop; see :func:`resolve_rebase_in_progress`."""
    # Completion is proved against the commit this replay is actually
    # landing on, pinned BEFORE the first resolver call -- not against
    # the target NAME. A resolution session runs for as long as an agent
    # takes, and in a shared-checkout fleet a sibling lands on the
    # mainline during that window routinely. Re-reading the name at the
    # end would then report a perfectly good replay as "not a descendant
    # of target" and throw it away for an endpoint merge, burying the
    # resolved work under a merge commit. A target that moved is the
    # bounded retry loop's job (it re-observes and replays onto the new
    # tip); it is not evidence that this rebase failed.
    base = _rebase_base_sha(root) or target
    stops_spent = 0
    while rebase_in_progress_at(root):
        if not _resolve_one_stop(root, target, resolver, stops_spent + 1):
            return False
        stops_spent += 1
        route = route_after_stop(stops_spent, not rebase_in_progress_at(root))
        if route == TERMINAL_RESOLVED:
            break
        if route == TERMINAL_ABANDONED:
            logger.warning(
                "conflict_resolution: rebase still conflicting after {} stop(s); "
                "abandoning in favour of the endpoint merge",
                stops_spent,
            )
            return False

    return verify_rebase_completed_at(root, base)


def _resolve_one_stop(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    stop_index: int,
) -> bool:
    """Resolve, prove and continue past ONE stop of the paused rebase.

    The order is the contract: observe the worktree BEFORE the resolver
    runs, so what it changed can be told apart from what the replay had
    already left dirty; check the scope BEFORE staging, so a resolver
    that strayed outside its conflicted paths is rejected rather than
    half-applied; and only then continue.

    Returns:
        Whether this stop landed. ``False`` routes the caller -- and
        through it :func:`resolve_rebase_in_progress` -- to the abort and
        endpoint-merge fallback.
    """
    stop = _read_stop(root, stop_index)
    if stop is None:
        return False
    before = _worktree_dirty_paths(root)
    if before is None:
        return False
    if _try_deterministic_resolution(root, stop):
        return _stage_and_prove(root, stop) and _continue_past(root, stop)
    if not resolver(root, target, stop):
        logger.info(
            "conflict_resolution: resolver declined rebase stop {} ({})",
            stop.stop_index,
            stop.sha,
        )
        return False
    return (
        _touched_nothing_unexpected(root, stop, before)
        and _stage_and_prove(root, stop)
        and _continue_past(root, stop)
    )


def _try_deterministic_resolution(root: Path, stop: RebaseStop) -> bool:
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
        if run_git(
            ("-C", str(submodule), "rev-parse", "--git-dir"),
            cwd=root,
            label="git-gitlink-dir",
        ).returncode != 0:
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


def _rebase_base_sha(root: Path) -> str | None:
    """Commit the paused rebase is replaying onto, or ``None`` if unreadable.

    Read through ``git rev-parse --git-path`` rather than by joining
    ``.git`` onto ``root``: this loop runs in LINKED worktrees, whose
    rebase state lives under the per-worktree git dir, not the common
    one. ``None`` is a benign answer -- the caller falls back to the
    branch name, i.e. exactly the pre-existing behaviour.
    """
    for relative in _REBASE_ONTO_FILES:
        result = run_git(
            ("rev-parse", "--git-path", relative),
            cwd=root,
            label="git-rebase-onto-path",
        )
        if result.returncode != 0:
            return None
        onto = Path(result.stdout.strip())
        if not onto.is_absolute():
            onto = root / onto
        try:
            sha = onto.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if sha:
            return sha
    return None


def _read_replay_progress(
    root: Path, *, read_text: Callable[[Path], str] | None = None
) -> tuple[int, int] | None:
    """Position of the current commit within the paused rebase's replay.

    Returns ``(current, total)`` -- the operator-facing "commit i/N" --
    or ``None`` when git's own rebase state cannot be read or does not
    describe a sensible position. Both backends are probed, because a
    repository rebasing with ``--apply`` keeps its counters under
    ``rebase-apply`` instead.

    Paths are resolved through ``git rev-parse --git-path`` rather than
    by joining ``.git`` onto ``root``, for exactly the reason
    :func:`_rebase_base_sha` documents: this loop runs in LINKED
    worktrees, whose rebase state lives under the per-worktree git dir.

    Purely cosmetic. The loop's termination is governed by
    :data:`~ralph.pipeline.conflict_resolution.graph.MAX_REBASE_CONFLICT_STOPS`
    alone, so an unreadable counter degrades the footer label and must
    never be able to fail a resolution.
    """
    reader = _read_rebase_state_text if read_text is None else read_text
    for current_file, total_file in _REBASE_PROGRESS_FILES:
        current = _read_rebase_state_int(root, current_file, read_text=reader)
        total = _read_rebase_state_int(root, total_file, read_text=reader)
        if current is None or total is None:
            continue
        if total >= 1 and 1 <= current <= total:
            return current, total
    return None


def _read_rebase_state_text(path: Path) -> str:
    """Read one rebase state file through the production filesystem seam."""
    return path.read_text(encoding="utf-8")


def _read_rebase_state_int(
    root: Path, relative: str, *, read_text: Callable[[Path], str] = _read_rebase_state_text
) -> int | None:
    """One integer out of a rebase state file, or ``None`` if unusable.

    Unreadable, absent, half-written and non-numeric all collapse to
    ``None``: the caller has a working fallback for every one of them.
    """
    result = run_git(
        ("rev-parse", "--git-path", relative),
        cwd=root,
        label="git-rebase-progress-path",
    )
    if result.returncode != 0:
        return None
    state_path = Path(result.stdout.strip())
    if not state_path.is_absolute():
        state_path = root / state_path
    try:
        return int(read_text(state_path).strip())
    except (OSError, ValueError):
        return None


def _read_stop(root: Path, stop_index: int) -> RebaseStop | None:
    """Describe the commit the rebase is currently stopped on.

    Returns ``None`` when the stopped commit's IDENTITY is unreadable,
    and when the paused rebase reports no conflicted path.

    Both are fail-closed on purpose. An unreadable ``REBASE_HEAD`` breaks
    two things at once: the prompt template gates its whole rebase-mode
    commit context on ``replaying_commit_sha``, so the resolver would be
    asked to fix a commit it is never told the identity of; and
    :func:`_advanced_to_a_new_stop` proves a stop landed by comparing
    SHAs, so an empty one would make ANY later readable SHA look like
    proof of advancement. A stop with no conflicted path is the other
    anomaly -- a stop this loop can act on always has unmerged entries in
    the index -- and there is nothing a resolver could repair there
    either. Declining hands the repository to the caller's abort path
    untouched rather than guessing.
    """
    sha = _rev_parse_rebase_head(root)
    if not sha:
        logger.warning(
            "conflict_resolution: could not read REBASE_HEAD for the paused "
            "rebase; declining to resolve"
        )
        return None
    conflicted = tuple(get_conflicted_files(repo_root=root))
    if not conflicted:
        logger.warning(
            "conflict_resolution: rebase is paused with no conflicted path; "
            "declining to resolve"
        )
        return None
    progress = _read_replay_progress(root)
    return RebaseStop(
        sha=sha,
        subject=_rebase_head_subject(root),
        conflicted_files=conflicted,
        stop_index=stop_index,
        stop_cap=MAX_REBASE_CONFLICT_STOPS,
        replay_index=None if progress is None else progress[0],
        replay_total=None if progress is None else progress[1],
    )


def _worktree_dirty_paths(root: Path) -> frozenset[str] | None:
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
    result = run_git(
        ("diff", "--name-only"),
        cwd=root,
        label="git-worktree-dirty-paths",
    )
    if result.returncode != 0:
        logger.warning(
            "conflict_resolution: could not read the worktree diff: {}",
            result.stderr.strip(),
        )
        return None
    return frozenset(line.strip() for line in result.stdout.splitlines() if line.strip())


def _touched_nothing_unexpected(
    root: Path,
    stop: RebaseStop,
    before: frozenset[str],
) -> bool:
    """Whether the resolver stayed inside the paths it was given.

    The prompt forbids editing any path that is not conflicted, and this
    is the enforcement that makes the prohibition real rather than
    advisory. Without it a disobedient resolver's unrelated edit is
    neither staged into the replayed commit -- :func:`_stage_and_prove`
    stages only ``stop.conflicted_files`` -- nor rejected, so it survives
    as dirty worktree state on top of a rebase that reported success.

    ``before`` is subtracted so a worktree that was already dirty when
    the stop was read is not blamed on the resolver; the gate is about
    what THIS round changed.
    """
    after = _worktree_dirty_paths(root)
    if after is None:
        return False
    unexpected = sorted(after - before - frozenset(stop.conflicted_files))
    if not unexpected:
        return True
    logger.warning(
        "conflict_resolution: resolver edited unrequested path(s) at stop {}: {}; "
        "rejecting the resolution",
        stop.stop_index,
        unexpected,
    )
    return False


def _stage_and_prove(root: Path, stop: RebaseStop) -> bool:
    """Stage the stop's paths and prove the conflicts are really gone.

    Staging is scoped to exactly the paths that conflicted -- never
    ``git add -A`` -- so an unrelated file the agent touched is not
    swept into the replayed commit.

    The marker scan runs AFTER staging deliberately: ``git add`` clears
    a file's unmerged bit even when ``<<<<<<<`` fences survive in its
    content, so an empty :func:`unmerged_paths` result is not by itself
    proof of a resolution. Both gates are kept; git is authoritative
    about the index, the textual scan about the content.
    """
    paths = list(stop.conflicted_files)
    if not stage_paths(root, paths):
        logger.warning(
            "conflict_resolution: failed to stage resolved paths for stop {}: {}",
            stop.stop_index,
            paths,
        )
        return False
    marked = paths_with_conflict_markers(root, paths)
    if marked:
        logger.warning(
            "conflict_resolution: conflict markers survive at stop {}: {}",
            stop.stop_index,
            marked,
        )
        return False
    remaining = unmerged_paths(root)
    if remaining:
        logger.warning(
            "conflict_resolution: unmerged paths remain at stop {}: {}",
            stop.stop_index,
            remaining,
        )
        return False
    return True


def _continue_past(root: Path, stop: RebaseStop) -> bool:
    """Run ``git rebase --continue`` for a proven-resolved stop.

    Two non-obvious outcomes both count as SUCCESS for this stop:

    * :class:`NoRebaseInProgressError` -- the rebase finished between the
      proof and the continuation, which is the state the loop drives
      towards.
    * A non-zero exit that is really the NEXT commit conflicting.
      ``git rebase --continue`` commits the resolved stop and then keeps
      replaying; if the very next commit conflicts, git reports the whole
      invocation as failed even though this stop landed. Treating that as
      a failure is what made a two-conflict branch unresolvable while a
      one-conflict branch worked -- the loop would decline on stop 1 and
      fall back to the endpoint merge, so exactly the messiest rebases
      never got the multi-stop resolution this module exists to provide.
    """
    try:
        continue_rebase_at(root)
    except NoRebaseInProgressError:
        return True
    except (ConflictRemainingError, RebaseContinuationError) as exc:
        if _advanced_to_a_new_stop(root, stop):
            logger.info(
                "conflict_resolution: stop {} landed; the rebase stopped again "
                "on the next commit",
                stop.stop_index,
            )
            return True
        logger.warning(
            "conflict_resolution: could not continue the rebase past stop {}: {}",
            stop.stop_index,
            exc,
        )
        return False
    return True


def _advanced_to_a_new_stop(root: Path, stop: RebaseStop) -> bool:
    """Whether the rebase moved on to a DIFFERENT commit than ``stop``.

    Identity, not exit code: ``REBASE_HEAD`` naming a commit other than
    the one just resolved is proof that git committed this stop and
    paused on a later one. A rebase that is no longer in progress is not
    a new stop -- that case is already handled by the caller's
    :class:`NoRebaseInProgressError` branch and by the loop's own
    completion check.
    """
    if not rebase_in_progress_at(root):
        return False
    current = _rev_parse_rebase_head(root)
    return bool(current) and current != stop.sha


def _rev_parse_rebase_head(root: Path) -> str:
    """SHA of the commit being replayed, or ``''`` when unreadable."""
    result = run_git(
        ("rev-parse", "REBASE_HEAD"),
        cwd=root,
        label="git-rebase-head-sha",
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _rebase_head_subject(root: Path) -> str:
    """Subject line of the commit being replayed, with a safe fallback."""
    result = run_git(
        ("log", "-1", "--format=%s", "REBASE_HEAD"),
        cwd=root,
        label="git-rebase-head-subject",
    )
    if result.returncode != 0:
        return _UNKNOWN_SUBJECT
    return result.stdout.strip() or _UNKNOWN_SUBJECT


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: C1
# ladder rung: 2
# AC-14 rationale: C11
# ladder rung: 2
# AC-14 rationale: C12
# ladder rung: 2
# AC-14 rationale: C15
# ladder rung: 2
# AC-14 rationale: C2
# ladder rung: 2
# AC-14 rationale: C3
# ladder rung: 2
# AC-14 rationale: C4
# ladder rung: 2
# AC-14 rationale: C6
# ladder rung: 2
# AC-14 rationale: C8
# ladder rung: 2
# AC-14 rationale: C9
# ladder rung: 1
# ----- end AC-14 catalog evidence -----
