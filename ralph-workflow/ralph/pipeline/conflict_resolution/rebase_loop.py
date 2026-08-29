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

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    conflict_stage_entries,
    paths_with_conflict_markers,
    stage_paths,
    staged_conflict_marker_paths,
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
    TERMINAL_ABANDONED,
    TERMINAL_RESOLVED,
    route_after_stop,
)
from ralph.pipeline.conflict_resolution.progress import (
    RebaseResolutionProgress,
    clear_progress,
    load_progress_for_rebase,
    save_progress,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig

#: Placeholder subject used when the stopped commit's subject could not
#: be read. The stop is still resolvable -- the conflicted paths are what
#: the agent actually needs -- so an unreadable subject must not abort it.
_UNKNOWN_SUBJECT = "(subject unavailable)"

#: Where git records the commit a paused rebase is replaying onto, newest
#: backend first. ``rebase-merge`` is the default backend; ``rebase-apply``
#: is still produced by ``--apply`` and by older gits.
_REBASE_ONTO_FILES = ("rebase-merge/onto", "rebase-apply/onto")

#: Where git records the tip a paused rebase is replaying FROM, same
#: backend order as :data:`_REBASE_ONTO_FILES`. Together the two pin the
#: replay for its whole lifetime, which is what makes them usable as the
#: progress sidecar's rebase identity.
_REBASE_ORIG_HEAD_FILES = ("rebase-merge/orig-head", "rebase-apply/orig-head")

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
    "active_rebase_resolution_session",
    "bind_active_rebase_resolution_session",
    "record_landed_stop",
    "resolution_session_from_config",
    "resolve_rebase_in_progress",
]


_ACTIVE_REBASE_RESOLUTION_SESSION: ContextVar[ResolutionSession | None] = ContextVar(
    "active_rebase_resolution_session", default=None
)


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
type RebaseStopResolver = Callable[["Path", str, RebaseStop], ResolutionOutcome | bool]


def _resolution_succeeded(result: ResolutionOutcome | bool) -> bool:
    """Project legacy injected boolean fakes onto the typed resolver contract."""
    return result.succeeded if isinstance(result, ResolutionOutcome) else result


def resolution_session_from_config(config: UnifiedConfig) -> ResolutionSession:
    """Snapshot immutable conflict-resolution limits for one complete rebase."""
    limits = config.conflict_resolution
    return ResolutionSession(
        inactivity_timeout_seconds=limits.inactivity_timeout_seconds,
        max_rounds_per_stop=limits.max_rounds_per_stop,
        max_rebase_conflict_stops=limits.max_rebase_conflict_stops,
        max_fallback_agents=limits.max_fallback_agents,
        total_resolution_cap_seconds=limits.total_resolution_cap_seconds,
    )



@contextmanager
def bind_active_rebase_resolution_session(session: ResolutionSession) -> Iterator[None]:
    """Expose one complete-rebase session to unchanged three-argument resolvers."""
    token = _ACTIVE_REBASE_RESOLUTION_SESSION.set(session)
    try:
        yield
    finally:
        _ACTIVE_REBASE_RESOLUTION_SESSION.reset(token)


def active_rebase_resolution_session() -> ResolutionSession | None:
    """Return the session bound by the owning rebase loop, if any."""
    return _ACTIVE_REBASE_RESOLUTION_SESSION.get()


def resolve_rebase_in_progress(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    *,
    stop_cap: int = 10,
    session: object | None = None,
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
    effective_session = session if isinstance(session, ResolutionSession) else None
    try:
        effective_cap = _configured_stop_cap(effective_session, stop_cap)
        if effective_session is None:
            return _resolve_stops(root, target, resolver, effective_cap)
        with bind_active_rebase_resolution_session(effective_session):
            return _resolve_stops(root, target, resolver, effective_cap)
    except Exception as exc:
        logger.warning(
            "conflict_resolution: rebase resolution loop failed for '{}': {}",
            target,
            exc,
        )
        return False


def _configured_stop_cap(session: ResolutionSession | None, fallback: int) -> int:
    """Use the configured stop cap when a typed resolution session carries it."""
    raw: object = getattr(session, "max_rebase_conflict_stops", fallback)
    return raw if isinstance(raw, int) and raw > 0 else fallback


def _resolve_stops(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    stop_cap: int,
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
    entry_landed = frozenset(_landed_shas_at_entry(root))
    skipped_entry_shas: set[str] = set()
    stops_spent = 0
    while rebase_in_progress_at(root):
        skipped_before = len(skipped_entry_shas)
        if not _resolve_one_stop(
            root,
            target,
            resolver,
            stops_spent + 1,
            stop_cap,
            entry_landed,
            skipped_entry_shas,
        ):
            return False
        if len(skipped_entry_shas) > skipped_before:
            # A stop this rebase had ALREADY landed was replayed past,
            # not resolved. ``stop_cap`` bounds resolver attempts, and no
            # resolver was spent here -- counting it meant a rebase
            # resumed after ``stop_cap`` landed stops was abandoned
            # before its first UNLANDED stop was ever offered to an
            # agent. Each sha can be skipped once, so this cannot spin.
            continue
        stops_spent += 1
        route = route_after_stop(stops_spent, not rebase_in_progress_at(root), stop_cap)
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
    stop_cap: int,
    entry_landed: frozenset[str],
    skipped_entry_shas: set[str],
) -> bool:
    """Resolve, prove and continue past ONE stop of the paused rebase.

    The order is the contract: observe the worktree BEFORE the resolver
    runs, so what it changed can be told apart from what the replay had
    already left dirty; revert out-of-scope edits BEFORE staging, so a
    resolver that strayed outside its conflicted paths does not poison
    the worktree, while the in-scope resolution can still land; and only
    then continue. Stops already recorded in the progress sidecar are
    skipped so a fresh process resumes at the first unlanded stop.

    Returns:
        Whether this stop landed. ``False`` routes the caller -- and
        through it :func:`resolve_rebase_in_progress` -- to the abort and
        endpoint-merge fallback.
    """
    stop = _read_stop(root, stop_index, stop_cap)
    if stop is None:
        # A paused rebase with nothing unmerged needs no agent: its
        # conflicts are already staged, most likely by a run that died
        # between proving the stop and continuing past it. Declining
        # left the rebase paused on disk and the run exited having
        # invoked nobody -- for a stop that only needed `--continue`.
        return _continue_already_staged_stop(root, stop_index)
    if stop.sha in entry_landed and stop.sha not in skipped_entry_shas:
        skipped_entry_shas.add(stop.sha)
        logger.info(
            "conflict_resolution: resumed from sidecar; skipping already-landed stop {}",
            stop.sha,
        )
        return _continue_past(root, stop)
    before = _worktree_dirty_paths(root)
    if before is None:
        return False
    if _try_deterministic_resolution(root, stop):
        if _stage_and_prove(root, stop) and _continue_past(root, stop):
            return True
        # Ralph's own mechanical attempt did not hold up. It is not the
        # resolver's attempt, so it must not consume the stop's only
        # chance: fall through and let an agent look at the conflict
        # rather than abandoning a stop no agent was ever offered.
        logger.info(
            "conflict_resolution: deterministic resolution of stop {} did not prove out; "
            "handing the stop to the resolver",
            stop.stop_index,
        )
    if not _resolution_succeeded(resolver(root, target, stop)):
        # "Declined" is the resolver's own verdict, and this branch is
        # reached by anything that did not resolve the stop -- including
        # a launch that never happened and a provider that was never
        # reachable. Say what is actually known here.
        logger.info(
            "conflict_resolution: rebase stop {} ({}) was not resolved",
            stop.stop_index,
            stop.sha,
        )
        return False
    return (
        _touched_nothing_unexpected(root, stop, before)
        and _stage_and_prove(root, stop)
        and _remove_ort_residue(root, stop.conflicted_files)
        and _continue_past(root, stop)
    )


def _continue_already_staged_stop(root: Path, stop_index: int) -> bool:
    """Continue a paused rebase whose stop is staged and marker-free.

    Only when the index really is clean: an unreadable ``REBASE_HEAD``
    or any surviving unmerged path still declines, because then the stop
    is not resolved, it is unreadable.
    """
    if not _stop_is_genuinely_staged(root, stop_index):
        return False
    logger.info(
        "conflict_resolution: stop {} is staged and its files are marker-free; "
        "continuing the rebase",
        stop_index,
    )
    try:
        continue_rebase_at(root)
    except NoRebaseInProgressError:
        return True
    except (ConflictRemainingError, RebaseContinuationError) as exc:
        logger.warning(
            "conflict_resolution: could not continue the already-staged stop {}: {}",
            stop_index,
            exc,
        )
        return False
    return True


def _stop_is_genuinely_staged(root: Path, stop_index: int) -> bool:
    """Whether a paused rebase's stop really is resolved and stageable.

    A clean INDEX is not enough. ``_stage_and_prove`` stages before it
    scans -- deliberately, because ``git add`` clears the unmerged bit --
    so a round refused for surviving markers leaves exactly this state:
    nothing unmerged, markers still in the files. Continuing without
    looking committed them, with no agent invoked and a log line
    claiming the stop was marker-free.
    """
    if not rebase_in_progress_at(root):
        return False
    if not _rev_parse_rebase_head(root):
        # No readable identity for the stopped commit: the fail-closed
        # decline, not a resolved stop.
        return False
    if [path for path in unmerged_paths(root) if path != "<unmerged-path-query-failed>"]:
        return False
    marked: list[str] = [
        *paths_with_conflict_markers(root, _staged_paths(root)),
        *staged_conflict_marker_paths(root),
    ]
    if marked:
        logger.warning(
            "conflict_resolution: stop {} is staged but conflict markers survive in {}; "
            "not continuing the rebase",
            stop_index,
            ", ".join(marked),
        )
        return False
    return True


def _staged_paths(root: Path) -> list[str]:
    """Paths staged against HEAD, i.e. what continuing would commit."""
    # ``-z``: git quotes a non-ASCII path otherwise, and the quoted
    # string cannot be opened by the marker scan that reads this list.
    result = run_git(
        ("diff", "--cached", "--name-only", "-z"),
        cwd=root,
        label="git-staged-paths",
    )
    if result.returncode != 0:
        return []
    return [entry for entry in result.stdout.split("\0") if entry]


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
        if (
            run_git(
                ("-C", str(submodule), "rev-parse", "--git-dir"),
                cwd=root,
                label="git-gitlink-dir",
            ).returncode
            != 0
        ):
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

    ``None`` is a benign answer -- the caller falls back to the branch
    name, i.e. exactly the pre-existing behaviour.
    """
    return _read_rebase_state_sha(root, _REBASE_ONTO_FILES, label="git-rebase-onto-path")


def _rebase_orig_head_sha(root: Path) -> str | None:
    """Feature tip the paused rebase is replaying, or ``None`` if unreadable."""
    return _read_rebase_state_sha(
        root, _REBASE_ORIG_HEAD_FILES, label="git-rebase-orig-head-path"
    )


def current_rebase_identity(root: Path) -> tuple[str | None, str | None]:
    """``(orig-head, onto)`` of the rebase paused at ``root``.

    The pair git pins for the whole replay, so it names one rebase and
    no other. ``(None, None)`` when no rebase is in progress -- which is
    exactly how :func:`record_landed_stop` recognises that the replay it
    was following has finished and has no progress left worth keeping.

    Read through ``git rev-parse --git-path`` rather than by joining
    ``.git`` onto ``root``: this loop runs in LINKED worktrees, whose
    rebase state lives under the per-worktree git dir, not the common
    one.
    """
    return _rebase_orig_head_sha(root), _rebase_base_sha(root)


def _read_rebase_state_sha(root: Path, relatives: tuple[str, ...], *, label: str) -> str | None:
    """First readable non-empty SHA among ``relatives`` rebase state files.

    ``relatives`` lists the same file under each rebase backend, newest
    first, so a repository rebasing with ``--apply`` answers as well as
    the default ``merge`` backend.
    """
    for relative in relatives:
        result = run_git(("rev-parse", "--git-path", relative), cwd=root, label=label)
        if result.returncode != 0:
            return None
        state_file = Path(result.stdout.strip())
        if not state_file.is_absolute():
            state_file = root / state_file
        try:
            # filesystem-read-ok: git reports one rebase metadata path per explicit conflict resolution attempt
            sha = state_file.read_text(encoding="utf-8").strip()
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


def _read_stop(root: Path, stop_index: int, stop_cap: int) -> RebaseStop | None:
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
            "conflict_resolution: rebase is paused with no conflicted path; declining to resolve"
        )
        return None
    progress = _read_replay_progress(root)
    return RebaseStop(
        sha=sha,
        subject=_rebase_head_subject(root),
        conflicted_files=conflicted,
        stop_index=stop_index,
        stop_cap=stop_cap,
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


def _porcelain_paths(blob: str) -> list[str]:
    """Paths from a ``--porcelain=v1 -z`` blob, renames included."""
    entries = [entry for entry in blob.split("\0") if entry]
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        paths.append(path)
        if "R" in code or "C" in code:
            # A rename/copy is followed by its source path.
            if index < len(entries):
                paths.append(entries[index])
                index += 1
    return paths


def _touched_nothing_unexpected(
    root: Path,
    stop: RebaseStop,
    before: frozenset[str],
) -> bool:
    """Whether the resolver stayed inside the paths it was given.

    The prompt forbids editing any path that is not conflicted, and this
    is the enforcement that makes the prohibition real rather than
    advisory. A disobedient resolver's unrelated edit is reverted so it
    cannot linger as dirty worktree state, while in-scope conflicted
    paths still stage through :func:`_stage_and_prove`.

    ``before`` is subtracted so a worktree that was already dirty when
    the stop was read is not blamed on the resolver; the gate is about
    what THIS round changed.
    """
    after = _worktree_dirty_paths(root)
    if after is None:
        return False
    unexpected = sorted(
        path
        for path in after - before - frozenset(stop.conflicted_files)
        if not _is_ralph_workspace_path(path)
    )
    if not unexpected:
        return True
    unexpected_paths = tuple(unexpected)
    if _revert_unrequested_paths(root, unexpected_paths):
        logger.warning(
            "conflict_resolution: resolver edited unrequested path(s) at stop {}: {}; "
            "reverted those paths and kept the in-scope resolution",
            stop.stop_index,
            unexpected,
        )
        return True
    logger.warning(
        "conflict_resolution: resolver edited unrequested path(s) at stop {}: {}; "
        "rejecting the resolution",
        stop.stop_index,
        unexpected,
    )
    return False


#: Ralph's own workspace directory, written DURING the resolution it is
#: judging: the prompt is rendered to `.agent/tmp/`, and artifacts,
#: transcripts and progress records land there too. Charging those to
#: the resolver rejected the resolution -- the agent never touched them,
#: and the run then abandoned a rebase it had actually resolved.
_RALPH_WORKSPACE_PREFIX = ".agent/"


def _is_ralph_workspace_path(path: str) -> bool:
    """Whether ``path`` is Ralph's own bookkeeping rather than the agent's."""
    # `lstrip("./")` would strip the leading dot itself, turning
    # ".agent/tmp/x" into "agent/tmp/x" and matching nothing.
    normalised = path.strip().removeprefix("./")
    return normalised == ".agent" or normalised.startswith(_RALPH_WORKSPACE_PREFIX)


def _restore_one_unrequested_path(root: Path, path: str) -> bool:
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
    try:
        target = root / path
        if target.is_dir():
            return False
        target.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _revert_unrequested_paths(root: Path, paths: tuple[str, ...]) -> bool:
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
    return all(_restore_one_unrequested_path(root, path) for path in paths)


def _landed_shas_at_entry(root: Path) -> tuple[str, ...]:
    """Sidecar SHAs this SAME rebase landed before the current process.

    Scoped to the paused rebase's own identity. A record left by any
    other rebase must not be read here either: its SHAs would make
    :func:`_resolve_one_stop` skip a genuinely unresolved stop and
    ``--continue`` straight past it.
    """
    feature_sha, target_sha = current_rebase_identity(root)
    progress = load_progress_for_rebase(root, feature_sha=feature_sha, target_sha=target_sha)
    if progress is None:
        return ()
    if progress.landed_shas and _is_at_the_first_replay(root):
        # The identity survives `git rebase --abort` intact -- abort
        # restores HEAD to orig-head, so a retry reproduces the very same
        # (orig-head, onto) pair and the record is accepted. But a rebase
        # that had landed stops cannot be sitting on its FIRST commit, so
        # this record describes an attempt that no longer exists. Reading
        # it would skip a genuinely unresolved stop and continue straight
        # past it, with no resolver ever offered the conflict.
        logger.warning(
            "conflict_resolution: discarding a progress record claiming {} landed stop(s) "
            "while the replay is back at its first commit; the earlier attempt was aborted",
            len(progress.landed_shas),
        )
        clear_progress(root)
        return ()
    return tuple(progress.landed_shas)


def _is_at_the_first_replay(root: Path) -> bool:
    """Whether the paused rebase is stopped on the first commit it replays."""
    position = _read_replay_progress(root)
    return position is not None and position[0] == 1


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
    marked: list[str] = [
        *paths_with_conflict_markers(root, paths),
        *staged_conflict_marker_paths(root),
    ]
    if marked:
        logger.warning(
            "conflict_resolution: conflict markers survive at stop {}: {}",
            stop.stop_index,
            ", ".join(sorted(set(marked))),
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


def _is_ort_residue_name(conflicted_name: str, candidate_name: str) -> bool:
    """Whether ``candidate_name`` is git's ort residue for ``conflicted_name``.

    ort parks a side under ``<path>~<LABEL>`` where LABEL is the ref it
    came from. An editor backup is ``<path>~`` or ``<path>~4~``, and
    deleting those destroys operator files that no side of the conflict
    ever mentioned -- which this glob was doing on every merge
    resolution and every rebase stop.
    """
    label = candidate_name[len(conflicted_name) + 1 :]
    if not label or label.endswith("~"):
        return False
    return not label.strip("0123456789") == ""


def _remove_ort_residue(root: Path, paths: tuple[str, ...]) -> bool:
    """Remove only untracked ``path~label`` files left by an ort D/F conflict."""
    for path in paths:
        candidate_parent = (root / path).parent
        try:
            candidates = tuple(
                candidate
                for candidate in candidate_parent.glob(f"{Path(path).name}~*")
                if _is_ort_residue_name(Path(path).name, candidate.name)
            )
        except OSError:
            return False
        for candidate in candidates:
            relative = candidate.relative_to(root).as_posix()
            tracked = run_git(
                ("ls-files", "--error-unmatch", "--", relative),
                cwd=root,
                label="git-ort-residue-tracked",
            )
            if tracked.returncode == 0:
                continue
            try:
                if candidate.is_dir():
                    return False
                candidate.unlink()
            except OSError:
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
        record_landed_stop(root, stop)
        return True
    except (ConflictRemainingError, RebaseContinuationError) as exc:
        if _advanced_to_a_new_stop(root, stop):
            logger.info(
                "conflict_resolution: stop {} landed; the rebase stopped again on the next commit",
                stop.stop_index,
            )
            record_landed_stop(root, stop)
            return True
        logger.warning(
            "conflict_resolution: could not continue the rebase past stop {}: {}",
            stop.stop_index,
            exc,
        )
        return False
    if _replay_produced_nothing(root, stop):
        return False
    record_landed_stop(root, stop)
    return True


def _replay_produced_nothing(root: Path, stop: RebaseStop) -> bool:
    """Whether continuing DROPPED the replayed commit instead of landing it.

    A resolution that leaves the replay identical to what it is being
    replayed onto makes git drop the commit -- and the loop counted that
    as the stop landing, so the commit disappeared from history while
    the rebase reported success. Deciding to keep one side of a
    modify/delete is the ordinary way to reach it.

    Only a stop whose commit is genuinely gone is refused: a rebase that
    is still in progress, or whose log cannot be read, is left to the
    existing handling.
    """
    # NOT gated on the rebase having finished: `git rebase --continue`
    # answers an emptied replay with `--skip`, which drops the commit and
    # stops on the NEXT one -- so a mid-rebase stop is exactly where a
    # commit disappears, and returning early here confined the guard to
    # the last stop of a rebase.
    result = run_git(
        ("log", "--format=%s", "-n", "200"),
        cwd=root,
        label="git-replayed-subjects",
    )
    if result.returncode != 0 or not stop.subject or not result.stdout.strip():
        # No readable log is not evidence that the commit is gone.
        return False
    if stop.subject in {line.strip() for line in result.stdout.splitlines()}:
        return False
    logger.warning(
        "conflict_resolution: the resolution of stop {} ({}) left nothing to replay, so git "
        "dropped the commit '{}'; refusing to report that as a landed stop",
        stop.stop_index,
        stop.sha[:8],
        stop.subject,
    )
    return True


def record_landed_stop(root: Path, stop: RebaseStop) -> None:
    """Persist a landed rebase stop so a later failure does not discard it.

    Stamped with the identity of the rebase that produced it, so the
    record can only ever be resumed from -- or counted as a reason to
    keep a conflicted rebase on disk -- by that same rebase.

    A stop that landed the LAST commit leaves no rebase in progress, and
    this is the call that notices: with no identity to stamp there is
    nothing left to resume, so the sidecar is deleted instead of
    written. Skipping that was the whole defect. ``git rebase
    --continue`` past the final stop raises
    :class:`NoRebaseInProgressError`, which this module correctly counts
    as the stop LANDING -- and the old code answered that success by
    writing a sidecar for a rebase that no longer existed, which then
    outlived it forever.
    """
    feature_sha, target_sha = current_rebase_identity(root)
    if feature_sha is None or target_sha is None:
        clear_progress(root)
        return
    progress = (
        load_progress_for_rebase(root, feature_sha=feature_sha, target_sha=target_sha)
        or RebaseResolutionProgress()
    )
    progress.record_landed(stop.sha)
    progress.remaining_paths = list(stop.conflicted_files)
    progress.feature_sha = feature_sha
    progress.target_sha = target_sha
    save_progress(root, progress)


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
