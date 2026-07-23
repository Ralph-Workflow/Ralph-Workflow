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

* auto-integration is enabled and a target branch resolves
  (:func:`~ralph.pipeline.auto_integrate.resolve_integration_target`
  -- the same stateless, local-refs-only resolution the seams use);
* HEAD is on a branch (a detached HEAD means a rebase or similar is in
  flight -- never interfere) and that branch is NOT the target;
* the worktree has no uncommitted tracked changes (same definition of
  clean as the boundary seams,
  :func:`~ralph.pipeline.auto_integrate_worktree_state._worktree_is_clean`);
* the current branch tip is a strict ancestor of the target tip, i.e.
  the move is a genuine fast-forward.

The mutation itself is ``git merge --ff-only <target-sha>`` via
:func:`~ralph.git.merge.fast_forward_via_worktree`, which is its own
atomic guard: it advances ref + index + working tree together and
refuses non-destructively when anything changed between the checks and
the merge (an agent started editing, a seam integration began, the
target moved). Every skip is silent-by-design at debug level; a landed
catch-up logs at info level.

Threading contract (mirrors :class:`ralph.pro_support.heartbeat.ProHeartbeatClient`):
the worker runs in a **daemon thread** so the process can always exit;
``start()`` is idempotent; ``stop()`` only sets a ``threading.Event``
and never joins, so shutdown can never block on a slow git subprocess.
The run loop starts the worker at pipeline start
(:func:`start_catchup_worker_if_enabled`) and stops it in the run-loop
cleanup path alongside the other background collaborators.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    fast_forward_via_worktree,
    is_ancestor,
    observe_branch_sha,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate import resolve_integration_target
from ralph.pipeline.auto_integrate_worktree_state import _worktree_is_clean

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig

#: Cadence of the background catch-up probe. Each tick costs a handful
#: of local git subprocesses (status / rev-parse / merge-base), so a
#: 30-second cadence is negligible next to the conflict-resolution
#: tokens an accumulated divergence costs later.
DEFAULT_CATCHUP_INTERVAL_SECONDS: float = 30.0

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

    ``git symbolic-ref --quiet --short HEAD`` is used instead of
    GitPython's ``active_branch`` because a detached HEAD (a rebase in
    flight, a direct SHA checkout) must be an ordinary skip here, not
    an exception: the catch-up worker fires on a timer and a rebase
    being in progress is one of its EXPECTED observations.
    """
    result = run_git(
        ("symbolic-ref", "--quiet", "--short", "HEAD"),
        cwd=root,
        label="git-catchup-current-branch",
    )
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name or None


def attempt_catchup_fast_forward(config: UnifiedConfig, root: Path) -> str:
    """One catch-up probe: fast-forward the checkout onto the target if free.

    Stateless -- every gate is re-derived from config and live git
    state, matching the seam contract that integration decisions are
    never cached across observations. Returns one of the ``CATCHUP_*``
    outcome tags; the repository is mutated only on
    :data:`CATCHUP_FAST_FORWARDED`.

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
    target = resolve_integration_target(config, root)
    if target is None:
        return CATCHUP_NO_TARGET
    current = _current_branch_name(root)
    if current is None:
        return CATCHUP_NOT_ON_BRANCH
    if current == target:
        return CATCHUP_ON_TARGET
    if not _worktree_is_clean(root):
        return CATCHUP_DIRTY
    return _fast_forward_if_strictly_behind(root, target, current)


def _fast_forward_if_strictly_behind(root: Path, target: str, current: str) -> str:
    """SHA-level half of the tick: land only a genuine fast-forward.

    Split from :func:`attempt_catchup_fast_forward` so each half stays
    under the return-count lint budget; the split line is the natural
    one between the config/branch gates (no SHA reads) and the
    observation-bound fast-forward decision.
    """
    target_sha, target_ok = observe_branch_sha(root, target)
    if not target_ok or target_sha is None:
        return CATCHUP_TARGET_UNREADABLE
    current_sha, current_ok = observe_branch_sha(root, current)
    if not current_ok or current_sha is None:
        return CATCHUP_HEAD_UNREADABLE
    if current_sha == target_sha:
        return CATCHUP_UP_TO_DATE
    if not is_ancestor(root, current_sha, target_sha):
        return CATCHUP_DIVERGED
    if fast_forward_via_worktree(root, target_sha):
        return CATCHUP_FAST_FORWARDED
    return CATCHUP_REFUSED


class AutoIntegrateCatchupWorker:
    """Bounded daemon-threaded catch-up loop over :func:`attempt_catchup_fast_forward`.

    Constructor parameters are explicit so tests can inject a recording
    ``tick`` callable and a short ``interval_seconds`` instead of
    monkeypatching module state. No I/O happens at construction time;
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
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._config = config
        self._root = root
        self._interval = float(interval_seconds)
        self._tick: Callable[[], str] = tick if tick is not None else self._default_tick
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _default_tick(self) -> str:
        return attempt_catchup_fast_forward(self._config, self._root)

    def start(self) -> None:
        """Spawn the daemon worker thread. Idempotent: a second call is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        thread = threading.Thread(
            target=self._run_loop,
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

    def _run_loop(self) -> None:
        # ``Event.wait`` doubles as the interval sleep AND the stop
        # signal, so a stop() from any thread interrupts the wait
        # immediately instead of burning out the remaining interval.
        # The first tick therefore fires one full interval AFTER
        # start() -- the run loop already performs a synchronous
        # startup integration, so an immediate tick would be redundant.
        while not self._stop_event.wait(timeout=self._interval):
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
        if outcome == CATCHUP_FAST_FORWARDED:
            logger.info(
                "auto_integrate catch-up: fast-forwarded the checkout onto the target"
            )
        else:
            logger.debug("auto_integrate catch-up: {}", outcome)


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
    "start_catchup_worker_if_enabled",
]
