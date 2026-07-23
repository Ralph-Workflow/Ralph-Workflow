"""Background catch-up fast-forward worker for auto-integration.

Between integration seams the shared target branch keeps moving (other
fleet agents land on it), while this agent's checkout only catches up
at the next clean seam. When the checkout has NO commits of its own the
catch-up is a pure ``git merge --ff-only`` -- no rebase, no conflict
resolution, no agent tokens. Waiting for the next seam to perform that
free move lets divergence accumulate, and divergence is exactly what
later costs a conflict-resolution agent invocation.

:class:`AutoIntegrateCatchupWorker` closes that gap: a bounded daemon
thread wakes every :data:`DEFAULT_CATCHUP_INTERVAL_SECONDS` seconds and
attempts one :func:`attempt_catchup_fast_forward` tick. A tick mutates
the repository ONLY when every one of these holds:

* auto-integration is enabled and a target branch resolves (same
  refs-only precedence as
  :func:`~ralph.pipeline.auto_integrate.resolve_integration_target`);
* HEAD is on a branch (a detached HEAD means a rebase or similar is in
  flight -- never interfere) and that branch is NOT the target;
* the current branch tip is a strict ancestor of the target tip, i.e.
  the move is a genuine fast-forward;
* the worktree has no uncommitted tracked changes (same tracked-only
  definition of clean as the boundary seams).

QUIET-PROBE CONTRACT. The tick fires every 30 seconds for the whole
run, so its skip paths must be invisible: no display lines, no log
lines at ANY level, and no :func:`~ralph.git.subprocess_runner.run_git`
subprocesses -- every ProcessManager spawn writes RUNNING/EXITED
lifecycle lines to the log, which turned the early run_git-based probes
into a 30-second drumbeat of log noise. All read-only observation
therefore happens IN-PROCESS via GitPython (the
:mod:`ralph.git.operations` precedent): branch name, target resolution,
and both tip SHAs are plain ref-file reads that spawn nothing. The
ancestry and cleanliness checks go through GitPython's own git wrapper
(not ProcessManager), and only run once the SHAs prove a fast-forward
is even possible. The single ProcessManager subprocess left is the
``git merge --ff-only`` that actually lands -- an event worth its log
line. ``merge --ff-only`` refuses every non-fast-forward and every
file overwrite non-destructively, and :func:`_still_safe_to_merge`
re-verifies branch identity and no-rebase-in-flight immediately before
the spawn -- see that function's docstring for the bounded residual
window this design accepts.

Threading contract (mirrors :class:`ralph.pro_support.heartbeat.ProHeartbeatClient`):
the worker runs in a **daemon thread** so the process can always exit;
``start()`` is idempotent; ``stop()`` only sets a ``threading.Event``
and never joins, so shutdown can never block on a slow git subprocess.
The thread parks in ``Event.wait`` between ticks and every git touch is
ref-file I/O or a subprocess wait, all of which release the GIL -- the
worker cannot stall the pipeline thread. The run loop starts the worker
at pipeline start (:func:`start_catchup_worker_if_enabled`) and stops
it in the run-loop cleanup path alongside the other background
collaborators.
"""

from __future__ import annotations

import threading
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import Repo, SymbolicReference
from loguru import logger

from ralph.git.merge import fast_forward_via_worktree

if TYPE_CHECKING:
    from collections.abc import Callable

    from git import Head

    from ralph.config.models import UnifiedConfig

#: Cadence of the background catch-up probe. Each tick costs a handful
#: of in-process ref reads (no subprocess, no log lines), so a
#: 30-second cadence is negligible next to the conflict-resolution
#: tokens an accumulated divergence costs later.
DEFAULT_CATCHUP_INTERVAL_SECONDS: float = 30.0

#: Target auto-detection candidates when no target is configured and
#: ``origin/HEAD`` does not name a local branch. MUST stay in lockstep
#: with ``ralph.pipeline.auto_integrate._AUTO_DETECT_TARGET_CANDIDATES``:
#: the catch-up moving the checkout toward a DIFFERENT branch than the
#: seams integrate onto would be actively harmful.
_AUTO_DETECT_TARGET_CANDIDATES: tuple[str, ...] = ("main", "master")

#: Tick outcome tags. Exactly one is returned per
#: :func:`attempt_catchup_fast_forward` call; only
#: :data:`CATCHUP_FAST_FORWARDED` means the repository changed.
CATCHUP_DISABLED = "disabled"
CATCHUP_NO_TARGET = "no-target"
CATCHUP_NOT_ON_BRANCH = "not-on-branch"
CATCHUP_ON_TARGET = "on-target"
CATCHUP_DIRTY = "worktree-dirty"
CATCHUP_TARGET_UNREADABLE = "target-unreadable"
CATCHUP_HEAD_UNREADABLE = "head-unreadable"
CATCHUP_UP_TO_DATE = "up-to-date"
CATCHUP_DIVERGED = "diverged"
CATCHUP_REFUSED = "refused"
CATCHUP_FAST_FORWARDED = "fast-forwarded"


def _current_branch_name(root: Path) -> str | None:
    """Name of the branch HEAD is on, or ``None`` when detached / unreadable.

    In-process ref read (``.git/HEAD``); spawns nothing. A detached
    HEAD (a rebase in flight, a direct SHA checkout) must be an
    ordinary skip here, not an exception: the catch-up worker fires on
    a timer and a rebase being in progress is one of its EXPECTED
    observations, so GitPython's ``active_branch`` ``TypeError`` (and
    any other failure to read HEAD) collapses to ``None``.
    """
    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.active_branch.name
    except Exception:
        return None
    finally:
        _close_repo(repo)


def resolve_integration_target(config: UnifiedConfig, root: Path) -> str | None:
    """Quiet in-process mirror of the seams' target resolution.

    Same precedence, same refs-only contract as
    :func:`ralph.pipeline.auto_integrate.resolve_integration_target`
    (configured target verbatim when it exists locally, else the
    ``origin/HEAD`` default-branch NAME when a local branch of that
    name exists, else the first of ``('main', 'master')`` that exists
    locally, else ``None``) -- but observed through GitPython ref reads
    instead of ``run_git`` subprocesses, honouring this module's
    quiet-probe contract. Its precedence table mirrors the seam resolver
    so the catch-up cannot drift toward a different branch than the
    seams land on.
    """
    repo: Repo | None = None
    try:
        repo = Repo(root)
        configured_attr: object = getattr(config.general, "auto_integrate_target", None)
        if isinstance(configured_attr, str) and configured_attr:
            if _local_branch_exists(repo, configured_attr):
                return configured_attr
            return None
        origin_default = _origin_head_branch_name(repo)
        if origin_default is not None and _local_branch_exists(repo, origin_default):
            return origin_default
        for candidate in _AUTO_DETECT_TARGET_CANDIDATES:
            if _local_branch_exists(repo, candidate):
                return candidate
        return None
    except Exception:
        return None
    finally:
        _close_repo(repo)


def _local_head(repo: Repo, name: str) -> Head | None:
    """The local branch named EXACTLY ``name``, or ``None``.

    Deliberately iterates ``repo.heads`` comparing ``.name`` instead of
    the tempting ``repo.heads[name]``: GitPython's ``IterableList``
    falls back to ``getattr`` for string keys, so branch names that
    collide with ``list`` method names (``append``, ``index``,
    ``count``, ``sort``, ``copy``, ``pop``) resolve to the BOUND METHOD
    -- a false "exists" for a missing branch and an unreadable SHA for
    a real one. Adversarial review reproduced both; exact name
    comparison has no such collision.
    """
    try:
        return next((head for head in repo.heads if head.name == name), None)
    except Exception:
        return None


def _local_branch_exists(repo: Repo, name: str) -> bool:
    """Whether ``refs/heads/<name>`` exists, via in-process ref lookup."""
    return _local_head(repo, name) is not None


def _origin_head_branch_name(repo: Repo) -> str | None:
    """Branch NAME behind ``origin/HEAD``, or ``None`` when unset.

    In-process read of the ``refs/remotes/origin/HEAD`` symbolic ref --
    the quiet twin of :func:`ralph.git.merge.resolve_origin_head_branch`
    (which shells out and would log). Only the NAME crosses this
    boundary; remote state never influences which local refs exist,
    matching the local-only integration contract.
    """
    try:
        sym = SymbolicReference(repo, "refs/remotes/origin/HEAD")
        ref_name = sym.reference.name
    except Exception:
        return None
    prefix = "origin/"
    if ref_name.startswith(prefix):
        return ref_name[len(prefix) :]
    return None


def observe_branch_sha(root: Path, name: str) -> tuple[str | None, bool]:
    """Read ``refs/heads/<name>``'s tip SHA in-process.

    Returns ``(sha, query_ok)`` with the same shape as
    :func:`ralph.git.merge.observe_branch_sha` (which shells out and
    would log): ``(None, True)`` when the branch does not exist,
    ``(None, False)`` when the repository itself cannot be read.
    """
    repo: Repo | None = None
    try:
        repo = Repo(root)
        head = _local_head(repo, name)
        if head is None:
            return None, True
        return head.commit.hexsha, True
    except Exception:
        return None, False
    finally:
        _close_repo(repo)


def is_ancestor(root: Path, ancestor_sha: str, descendant_sha: str) -> bool:
    """Whether ``ancestor_sha`` is an ancestor of ``descendant_sha``.

    Delegates to GitPython's ``merge-base --is-ancestor`` wrapper --
    a git subprocess, but NOT a ProcessManager one, so it emits no
    lifecycle log lines. Only reached once the two SHAs are known to
    differ. Fails closed (``False``) so an unreadable repository can
    never be mistaken for "safe to fast-forward".
    """
    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.is_ancestor(repo.commit(ancestor_sha), repo.commit(descendant_sha))
    except Exception:
        return False
    finally:
        _close_repo(repo)


def _worktree_is_clean(root: Path) -> bool:
    """True when no uncommitted TRACKED modification is present.

    Runs the seams' EXACT probe -- ``git status --porcelain
    --untracked-files=no`` (see
    ``ralph.pipeline.auto_integrate_worktree_state._worktree_is_clean``)
    -- through GitPython's own git wrapper, so it stays off the
    ProcessManager/log path while giving byte-identical answers. An
    earlier revision used ``repo.is_dirty(untracked_files=False)``,
    but adversarial review showed the two disagree in the UNSAFE
    direction on line-ending-only modifications under
    ``core.autocrlf``: ``is_dirty`` applies the clean filter (clean)
    while ``status`` stat-matches (dirty), and the seam's verdict must
    win. Untracked files deliberately do not block: ``merge --ff-only``
    refuses non-destructively per-file for any untracked path it would
    overwrite. Fails closed (``False``) on any error so the worker
    never fast-forwards a worktree it cannot prove clean.
    """
    repo: Repo | None = None
    try:
        repo = Repo(root)
        # ``repo.git.<cmd>`` is GitPython's dynamic dispatch and types
        # as Any; the cast is the sanctioned disallow_any_expr wrapper.
        status_out = cast(
            "str", repo.git.status("--porcelain", "--untracked-files=no")
        )
        return not status_out.strip()
    except Exception:
        return False
    finally:
        _close_repo(repo)


def _close_repo(repo: Repo | None) -> None:
    if repo is not None:
        with suppress(Exception):
            repo.close()


def attempt_catchup_fast_forward(config: UnifiedConfig, root: Path) -> str:
    """One catch-up probe: fast-forward the checkout onto the target if free.

    Stateless -- every gate is re-derived from config and live git
    state, matching the seam contract that integration decisions are
    never cached across observations. Returns one of the ``CATCHUP_*``
    outcome tags; the repository is mutated only on
    :data:`CATCHUP_FAST_FORWARDED`.

    Gate order is cheapest-first under the quiet-probe contract: the
    config read and every ref observation are in-process and free, so
    the two checks that shell out through GitPython (ancestry, then
    cleanliness) run only once the SHAs prove the checkout is strictly
    behind the target -- the steady states (up to date, diverged-with-
    own-commits, on the target) never spawn anything at all.

    The final ``merge --ff-only`` is handed the observed target SHA,
    not the ref name, so a target that advances between the ancestry
    check and the merge can at worst land the slightly older
    (still-ancestor) tip -- never a non-fast-forward. If the CHECKOUT
    moved in between (an agent committed), ``merge --ff-only`` against
    a now-non-descendant SHA refuses without mutating anything and the
    tick reports :data:`CATCHUP_REFUSED`.
    """
    enabled_raw: object = getattr(config.general, "auto_integrate_enabled", True)
    if not (isinstance(enabled_raw, bool) and enabled_raw):
        return CATCHUP_DISABLED
    current = _current_branch_name(root)
    if current is None:
        return CATCHUP_NOT_ON_BRANCH
    target = resolve_integration_target(config, root)
    if target is None:
        return CATCHUP_NO_TARGET
    if current == target:
        return CATCHUP_ON_TARGET
    return _fast_forward_if_strictly_behind(root, target, current)


def _fast_forward_if_strictly_behind(root: Path, target: str, current: str) -> str:
    """SHA-level half of the tick: land only a genuine fast-forward.

    Split from :func:`attempt_catchup_fast_forward` so each half stays
    under the return-count lint budget; the split line is the natural
    one between the config/branch-name gates and the SHA observations.
    """
    target_sha, skip_reason = _observe_strictly_behind(root, target, current)
    if target_sha is None:
        return skip_reason
    if not _worktree_is_clean(root):
        return CATCHUP_DIRTY
    if not _still_safe_to_merge(root, current):
        return CATCHUP_REFUSED
    if fast_forward_via_worktree(root, target_sha):
        return CATCHUP_FAST_FORWARDED
    return CATCHUP_REFUSED


def _still_safe_to_merge(root: Path, expected_branch: str) -> bool:
    """Last-instant re-check before spawning the merge.

    ``merge --ff-only`` is NOT a complete guard on its own: adversarial
    review demonstrated that git happily fast-forwards a DETACHED
    rebase HEAD (a rebase that began after this tick's earlier gates,
    stopped with a clean transient tree at an ancestor of the target),
    silently changing the base the rebase resumes on. There is no lock
    shared with the pipeline thread's seam integration, so this
    re-check -- all in-process reads, immediately before the spawn --
    shrinks that tens-of-milliseconds window to the sub-millisecond gap
    between this read and the merge process starting: HEAD must still
    be the SAME branch the SHA gates were computed for, and no
    ``rebase-merge`` / ``rebase-apply`` state may exist in the gitdir.
    The residual gap is accepted and bounded: a rebase can only begin
    inside it via the seam or an agent, the originals stay reachable
    via reflog/ORIG_HEAD, and ``merge --ff-only`` still refuses every
    non-fast-forward and every overwrite.
    """
    repo: Repo | None = None
    try:
        repo = Repo(root)
        git_dir = Path(repo.git_dir)
        if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
            return False
        return repo.active_branch.name == expected_branch
    except Exception:
        return False
    finally:
        _close_repo(repo)


def _observe_strictly_behind(
    root: Path, target: str, current: str
) -> tuple[str | None, str]:
    """Observe both tips; return ``(target_sha, skip_reason)``.

    Exactly one slot is populated: the target SHA when the checkout is
    strictly behind the target (a fast-forward is possible), else the
    skip tag explaining why not. Pure observation -- in-process ref
    reads plus the GitPython ancestry check -- so the caller owns every
    mutation decision.
    """
    target_sha, target_ok = observe_branch_sha(root, target)
    if not target_ok or target_sha is None:
        return None, CATCHUP_TARGET_UNREADABLE
    current_sha, current_ok = observe_branch_sha(root, current)
    if not current_ok or current_sha is None:
        return None, CATCHUP_HEAD_UNREADABLE
    if current_sha == target_sha:
        return None, CATCHUP_UP_TO_DATE
    if not is_ancestor(root, current_sha, target_sha):
        return None, CATCHUP_DIVERGED
    return target_sha, ""


class AutoIntegrateCatchupWorker:
    """Bounded daemon-threaded catch-up loop over :func:`attempt_catchup_fast_forward`.

    Constructor parameters are explicit so tests can inject a recording
    ``tick`` callable and deterministic ``wait`` function instead of
    waiting on a real clock. No I/O happens at construction time;
    ``start()`` launches the daemon thread and ``stop()`` is an
    idempotent event-set that never joins (the thread is daemonic, and
    joining could block shutdown on a slow git subprocess).
    """

    def __init__(
        self,
        config: UnifiedConfig,
        root: Path,
        *,
        interval_seconds: float = DEFAULT_CATCHUP_INTERVAL_SECONDS,
        tick: Callable[[], str] | None = None,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._config = config
        self._root = root
        self._interval = float(interval_seconds)
        self._tick: Callable[[], str] = tick if tick is not None else self._default_tick
        self._stop_event = threading.Event()
        self._wait = wait if wait is not None else self._stop_event.wait
        self._thread: threading.Thread | None = None

    def _default_tick(self) -> str:
        return attempt_catchup_fast_forward(self._config, self._root)

    def start(self) -> None:
        """Spawn the daemon worker thread. Idempotent: a second call is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        thread = threading.Thread(
            target=self.run,
            name="ralph-auto-integrate-catchup",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Signal the worker to exit at its next wakeup. Idempotent.

        Deliberately does NOT ``join()``: the worker is daemonic and a
        join could block process shutdown behind a git subprocess.
        """
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run(self) -> None:
        """Run cadence ticks until the injected wait reports a stop."""
        # ``Event.wait`` doubles as the interval sleep AND the stop
        # signal, so a stop() from any thread interrupts the wait
        # immediately instead of burning out the remaining interval.
        # The first tick therefore fires one full interval AFTER
        # start() -- the run loop already performs a synchronous
        # startup integration, so an immediate tick would be redundant.
        while not self._wait(self._interval):
            self._tick_once()

    def _tick_once(self) -> None:
        try:
            outcome = self._tick()
        except Exception as tick_exc:
            logger.warning(
                "auto_integrate catch-up tick failed (will retry next interval): {}",
                tick_exc,
            )
            return
        # Skips are deliberately UNLOGGED (quiet-probe contract): the
        # tick fires every 30 seconds for the whole run and a per-tick
        # skip line (even at debug level) is pure noise in the log
        # stream. Only the tick that actually moved the checkout says
        # anything.
        if outcome == CATCHUP_FAST_FORWARDED:
            logger.info(
                "auto_integrate catch-up: fast-forwarded the checkout onto the target"
            )


def start_catchup_worker_if_enabled(
    config: UnifiedConfig,
    root: Path,
    *,
    interval_seconds: float = DEFAULT_CATCHUP_INTERVAL_SECONDS,
) -> AutoIntegrateCatchupWorker | None:
    """Construct and start the catch-up worker when auto-integration is on.

    Returns ``None`` (and spawns nothing) when
    ``config.general.auto_integrate_enabled`` is not a true boolean --
    the same defensive read every seam uses -- so a disabled run never
    pays the background git probes at all. The caller owns the returned
    worker and must call ``stop()`` on every exit path.
    """
    enabled_raw: object = getattr(config.general, "auto_integrate_enabled", True)
    if not (isinstance(enabled_raw, bool) and enabled_raw):
        return None
    worker = AutoIntegrateCatchupWorker(config, root, interval_seconds=interval_seconds)
    worker.start()
    return worker


__all__ = [
    "CATCHUP_DIRTY",
    "CATCHUP_DISABLED",
    "CATCHUP_DIVERGED",
    "CATCHUP_FAST_FORWARDED",
    "CATCHUP_HEAD_UNREADABLE",
    "CATCHUP_NOT_ON_BRANCH",
    "CATCHUP_NO_TARGET",
    "CATCHUP_ON_TARGET",
    "CATCHUP_REFUSED",
    "CATCHUP_TARGET_UNREADABLE",
    "CATCHUP_UP_TO_DATE",
    "DEFAULT_CATCHUP_INTERVAL_SECONDS",
    "AutoIntegrateCatchupWorker",
    "attempt_catchup_fast_forward",
    "resolve_integration_target",
    "start_catchup_worker_if_enabled",
]
