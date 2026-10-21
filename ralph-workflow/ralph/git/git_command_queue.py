"""Per-repository git command queue and worktree lease handshake.

This module owns the SINGLE point every ``ralph.git`` code path MUST route
through for any git interaction that touches the on-disk object store /
refs / index:

* a :class:`GitCommandQueue` keyed by ``git rev-parse --git-common-dir`` —
  every linked worktree of one repository shares ONE queue (shared
  index, refs, lock files — serializing them is correctness), while
  unrelated repositories keep independent queues;
* a SHARED health signal on that queue — consecutive ``TimeoutExpired``
  exceptions raise a per-queue counter that downstream callers read or
  reset (used by the catch-up ticker to stop stacking 120 s timeouts on
  top of an already-overloaded repository);
* a per-repository :class:`WorktreeLease` handshake — the agent-phase
  helpers acquire it so background mutators can read its absence and
  skip a tick instead of racing an in-flight agent.

GitPython ``repo.git.*`` dispatch and the :func:`run_git` subprocess
path both go through :func:`route_git_method` and
:func:`route_run_git` respectively. Both call into the SAME queue, so
producers cannot interleave. Each queued call records its timeout
outcome; the next caller sees the elevated counter and bails (the
backoff is the read, not a sleep — the existing ``run_git`` 120 s
ceiling remains the hard wall).

The worktree lease uses ``os.open(O_CREAT|O_EXCL)`` on a lock-file
under ``<git_dir>/ralph/agent-lease.lock``. ``O_EXCL`` gives
POSIX-atomic acquisition: a second ``begin_agent_lease`` call on the
same repo fails fast with :class:`WorktreeLeaseUnavailable`. Tests and
background callers use :func:`try_acquire_lease` for the non-throwing
variant and treat ``None`` as "skip this tick".
"""

from __future__ import annotations

import contextlib
import errno
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from git import Repo

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ralph.git.git_run_result import GitRunResult
    from ralph.git.subprocess_runner import GitRunOptions


# ---------------------------------------------------------------------------
# Module-level constants (declared at top so the import-time invariants
# below survive any subsequent refactor that moves them).
# ---------------------------------------------------------------------------

#: Default cap on retained queue statistics. The execution log ring is what a
#: future operator-facing "what was the queue health in the last 256 calls"
#: surface would read; bounded so a long unattended run cannot leak memory.
_HEALTH_LOG_CAP: int = 256

#: Default counter ceiling: how many consecutive ``TimeoutExpired`` results
#: the queue tracks before saturating. Callers treat saturation as "every
#: subsequent call should treat this queue as stalled".
_MAX_CONSECUTIVE_TIMEOUTS: int = 64

#: Worktree lease lock-filename (placed under ``<git_dir>/ralph/``).
_AGENT_LEASE_FILENAME: str = "agent-lease.lock"

#: How many seconds a stale lease may remain before a new agent steals it.
#: 5x the ``run_git`` ceiling gives ample headroom for the held agent's
#: wrap-around; anything longer than this is treated as crash-leftover
#: and silently reclaimable.
_LEASE_STALE_SECONDS: float = 600.0


@dataclass(frozen=True)
class QueuedCallOutcome:
    """A single observed outcome for a queued git call.

    The queue records one of these for every routed call so tests can prove
    serialization and the health state can be exposed to operators. The
    ``kind`` field is the only thing the ``consecutive_timeout`` counter
    branches on — any ``TIMEOUT`` advances, any other kind resets to zero.

    Attributes:
        kind: ``"success"`` for a clean return; ``"timeout"`` for a
            :class:`subprocess.TimeoutExpired` or :class:`GitCommandError`
            that the queue classifies as a stall; ``"error"`` for any other
            exception. The success/timeout/error split is the only axis the
            health counter reads; detailed classification belongs to the
            higher layer.
    """

    kind: Literal["success", "timeout", "error"]


class GitCommandQueue:
    """Per-common-dir serial command queue with a shared health signal.

    Every git operation that touches the repository's on-disk state
    (refs, the index, the object store, the working tree metadata) MUST
    pass through this queue. The serializing lock prevents two callers
    from interleaving git invocations — git does not take a
    process-wide repository lock, so a parallel ``git status`` and
    ``git commit`` can race the index lock and leave
    ``.git/index`` truncated.

    The queue's lifecycle is process-wide: a single
    :class:`GitCommandQueue` instance per resolved common-directory
    string, lazily created by :func:`get_queue_for_repo`. Linked
    worktrees of the same repo collide on the same instance; unrelated
    repos keep independent queues. Bounded by ``_HEALTH_LOG_CAP`` and
    ``_MAX_CONSECUTIVE_TIMEOUTS`` (the import-time invariants in this
    module pin both constants positive).

    Attributes:
        common_dir: Absolute path to ``git rev-parse --git-common-dir``.
            Two callers land on the same queue iff their resolved
            ``common_dir`` strings compare equal.
        execute_lock: Re-entrant lock held for the duration of every
            routed call. ``run_git`` callers and GitPython
            ``repo.git.*`` callers funnel through the same lock, so
            ``git status`` cannot interleave with ``git commit``.
        consecutive_timeouts: Monotonically-increasing counter that
            resets to zero on the first non-timeout. Saturated at
            ``_MAX_CONSECUTIVE_TIMEOUTS``. Tickers use this to stop
            stacking 120 s timeouts on an already-overloaded
            repository.
    """

    common_dir: Path
    execute_lock: threading.RLock
    consecutive_timeouts: int
    outcome_log: deque[QueuedCallOutcome]  # bounded-accumulator-ok: FIFO cap _HEALTH_LOG_CAP

    def __init__(self, common_dir: Path) -> None:
        self.common_dir = common_dir
        self.execute_lock = threading.RLock()
        self.consecutive_timeouts = 0
        self.outcome_log = deque(maxlen=_HEALTH_LOG_CAP)

    def record_outcome(self, kind: Literal["success", "timeout", "error"]) -> None:
        """Update the health signal and bounded outcome log."""
        if kind == "timeout":
            self.consecutive_timeouts = min(self.consecutive_timeouts + 1, _MAX_CONSECUTIVE_TIMEOUTS)
        else:
            self.consecutive_timeouts = 0
        self.outcome_log.append(QueuedCallOutcome(kind=kind))

    def health_factor(self) -> int:
        """Return the consecutive-timeout counter for outside callers.

        Tests and the catch-up ticker read this to decide whether
        another call should even attempt acquisition. A non-zero value
        means the last call (or the last ``_MAX_CONSECUTIVE_TIMEOUTS``
        in a row) timed out; the queue is treated as overloaded until
        the next non-timeout resets it.
        """
        return self.consecutive_timeouts

    def reset_health(self) -> None:
        """Zero the consecutive-timeout counter; clear the bounded log.

        Tests call this after asserting on a synthetic overload so the
        next test does not see the residue. Production code rarely
        invokes it: the counter resets naturally on the next
        successful call.
        """
        self.consecutive_timeouts = 0
        self.outcome_log.clear()


# Import-time invariants: pin the bounds positive and finite.
if not (_HEALTH_LOG_CAP > 0):
    raise RuntimeError(f"_HEALTH_LOG_CAP must be positive; got {_HEALTH_LOG_CAP!r}")
if not (_MAX_CONSECUTIVE_TIMEOUTS > 0):
    raise RuntimeError(
        f"_MAX_CONSECUTIVE_TIMEOUTS must be positive; got {_MAX_CONSECUTIVE_TIMEOUTS!r}"
    )
if not (_LEASE_STALE_SECONDS > 0):
    raise RuntimeError(
        f"_LEASE_STALE_SECONDS must be positive; got {_LEASE_STALE_SECONDS!r}"
    )


# ---------------------------------------------------------------------------
# Queue registry
# ---------------------------------------------------------------------------


@dataclass
class _QueueRegistry:
    """Process-wide registry of :class:`GitCommandQueue` instances.

    Keyed by the resolved common-directory path of each repository
    (so two ``Repo`` handles pointing at the same repository — the main
    checkout and any of its linked worktrees — collide on one queue).
    The registry is itself thread-safe because ``get_queue_for_repo``
    may be called concurrently from many phases.
    """

    _queues: dict[str, GitCommandQueue] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get_or_create(self, common_dir: Path) -> GitCommandQueue:
        """Return the queue for ``common_dir``, creating it on first use."""
        key = str(common_dir)
        existing: GitCommandQueue | None
        with self._lock:
            existing = self._queues.get(key)
            if existing is not None:
                return existing
            created = GitCommandQueue(common_dir)
            self._queues[key] = created
            return created

    def reset_all(self) -> None:
        """Clear every registered queue.

        Tests use this between cases so registration from one case does
        not leak into the next. Never call from production code:
        unrelated repositories can hold live call state.
        """
        with self._lock:
            for queue in self._queues.values():
                queue.reset_health()
            self._queues.clear()


_REGISTRY: _QueueRegistry = _QueueRegistry()


def _resolve_common_dir(repo_or_path: Repo | Path | str) -> Path:
    """Return the canonical common-directory path for ``repo_or_path``.

    Accepts a :class:`git.Repo` (reads ``git_dir``), a :class:`Path`,
    or a string. The result is resolved to an absolute real path so
    two callers using different aliases (e.g. ``/foo`` and
    ``/foo/.git``) land on the same queue key.
    """
    if isinstance(repo_or_path, Repo):
        return _normalize_git_dir(Path(repo_or_path.git_dir))
    git_dir_attr = getattr(repo_or_path, "git_dir", None)
    if isinstance(git_dir_attr, str):
        # Repo-like boundary: tests and threaded re-entry may carry a
        # duck-typed Repo proxy; ``git_dir`` is the only attribute read.
        return _normalize_git_dir(Path(git_dir_attr))
    path = Path(repo_or_path).expanduser()
    return _normalize_git_dir(path)


#: Minimum path depth for a ``.../.git/worktrees/<name>`` trailing segment
#: to be recognized as a linked-worktree git dir (``.git``/``worktrees``/name).
_WORKTREE_GIT_DIR_MIN_PARTS: int = 3

#: Number of trailing path parts in ``.../.git/worktrees/<name>`` above the
#: common git dir (``<name>`` and ``worktrees``).
_WORKTREE_GIT_DIR_UP_LEVELS: int = 2


def _normalize_git_dir(path: Path) -> Path:
    """Canonicalize a git-dir candidate to its common-dir form.

    Accepts any of: a repo working directory (descends into ``.git``,
    following a worktree ``.git`` file's ``gitdir:`` pointer), an actual
    git directory, or a linked-worktree git directory
    (``.../.git/worktrees/<name>`` — collapsed to the common dir). The
    goal: every caller shape lands on ONE canonical key so two callers
    using different aliases share a queue. Falls back to the resolved
    input when detection is uncertain.
    """
    resolved = path.resolve(strict=False)
    git_marker = resolved / ".git"
    if git_marker.is_dir():
        resolved = git_marker
    elif git_marker.is_file():
        try:
            first_line = git_marker.read_text(encoding="utf-8", errors="replace").split(
                "\n", 1
            )[0]
        except OSError:
            first_line = ""
        if first_line.startswith("gitdir:"):
            pointed = Path(first_line[len("gitdir:") :].strip())
            if not pointed.is_absolute():
                pointed = resolved / pointed
            resolved = pointed.resolve(strict=False)
    parts = resolved.parts
    if len(parts) >= _WORKTREE_GIT_DIR_MIN_PARTS and parts[-2] == "worktrees":
        common = resolved
        for _ in range(_WORKTREE_GIT_DIR_UP_LEVELS):
            common = common.parent
        return common.resolve(strict=False)
    return resolved


def get_queue_for_repo(repo_or_path: Repo | Path | str) -> GitCommandQueue:
    """Return the queue for ``repo_or_path``; create it lazily.

    Two callers using paths that resolve to the same common-dir land
    on the same instance — including a main checkout and any of its
    linked worktrees. Unrelated repos keep independent queues. The
    argument is read structurally (a ``Repo``, a ``Path``, or a
    path-like/``str``), so tests may pass duck-typed stand-ins.
    """
    common = _resolve_common_dir(repo_or_path)
    return _REGISTRY.get_or_create(common)


def reset_all_queues() -> None:
    """Discard every registered queue. Test-only.

    Production code never needs this; tests call it from a fixture
    teardown so registration from one case cannot leak across.
    """
    _REGISTRY.reset_all()


# ---------------------------------------------------------------------------
# Adapter for GitPython ``repo.git.<method>(...)`` callers
# ---------------------------------------------------------------------------


def route_git_method(
    repo: Repo,
    method_name: str,
    args: Sequence[object] = (),
    kwargs: Mapping[str, object] | None = None,
) -> object:
    """Invoke ``repo.git.<method_name>(*args, **kwargs)`` through the queue.

    The lock acquisition is the entire serialization contract: a
    second caller cannot enter until the first one observes its
    outcome. The underlying method attribute is resolved INSIDE the
    critical section so a concurrent monkey-patch (test seam) cannot
    swap it between the resolution and the call.

    The ``repo`` argument is accepted structurally: only ``repo.git``
    (dispatched via ``getattr``) and the repo's git-dir path (queue
    key) are read, so tests may pass a duck-typed stand-in.

    The return value mirrors the underlying method's return value —
    GitPython returns ``str`` for textual subcommands, ``True``/``None``
    for state-changing ones, ``list[...]`` for ``--format``
    enumerations, etc. The router therefore returns ``object``; the
    caller (``cast("str", ...)`) carries the proof obligation.

    Args:
        repo: A live :class:`git.Repo`. The caller is responsible for
            opening and closing it; the queue does not take ownership.
        method_name: Name of the ``repo.git.*`` method to dispatch
            (e.g. ``"status"``, ``"config"``, ``"reset"``). Resolved
            via ``getattr`` inside the lock.
        args: Positional arguments to forward to the method.
        kwargs: Keyword arguments to forward to the method.

    Returns:
        Whatever the underlying ``repo.git.<method_name>`` returns.

    Raises:
        git.exc.GitCommandError: Re-raised unchanged from the
            underlying call.
        OSError: Re-raised for filesystem-level failures.
        subprocess.TimeoutExpired: Re-raised unchanged.
    """
    queue = get_queue_for_repo(cast("Repo | Path | str", repo))
    call_kwargs: Mapping[str, object] = kwargs if kwargs is not None else {}
    with queue.execute_lock:
        git_attr: object = getattr(repo, "git", None)
        if git_attr is None:
            queue.record_outcome("error")
            msg = f"repo {repo!r} has no .git attribute"
            raise AttributeError(msg)
        try:
            raw_method: object = getattr(git_attr, method_name)
            method: Callable[..., object] = cast("Callable[..., object]", raw_method)  # type: ignore[misc,explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        except AttributeError as exc:
            queue.record_outcome("error")
            raise AttributeError(
                f"repo.git has no method named {method_name!r}"
            ) from exc
        try:
            result = method(*args, **call_kwargs)
        except BaseException as exc:
            queue.record_outcome(_classify_exception(exc))
            raise
        else:
            queue.record_outcome("success")
            return result


def _classify_exception(exc: BaseException) -> Literal["timeout", "error"]:
    """Bucket a routed exception into timeout vs error."""
    if isinstance(exc, TimeoutError):
        # Covers the builtin and GitPython/GitCommandLine timeouts.
        return "timeout"
    if type(exc).__qualname__ == "TimeoutExpired":
        # subprocess.TimeoutExpired (not a TimeoutError subclass on <3.11).
        return "timeout"
    return "error"


# ---------------------------------------------------------------------------
# Adapter for :func:`run_git` subprocess callers
# ---------------------------------------------------------------------------


def route_run_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None = None,
) -> GitRunResult:
    """Execute :func:`run_git` through the queue with timeout classification.

    Acquires the queue lock for the repository identified by ``cwd``
    (or — when ``cwd`` is ``None`` — the GLOBAL fallback queue, which
    should not happen in practice because ``run_git`` always carries a
    cwd). On ``subprocess.TimeoutExpired`` the queue's
    ``consecutive_timeouts`` counter advances so subsequent callers
    see the elevated health factor before they even attempt
    acquisition.

    The 120 s ceiling from :func:`run_git`
    (``GIT_SUBPROCESS_TIMEOUT_SECONDS``) remains the hard wall for the
    child; the queue adds nothing to that ceiling — it stacks NO sleep.
    """
    from ralph.git.subprocess_runner import (  # noqa: PLC0415
        _spawn_git,  # reason: lazy import breaks queue<->runner cycle
    )

    queue = get_queue_for_repo(cwd if cwd is not None else Path.cwd())
    with queue.execute_lock:
        try:
            result = _spawn_git(args, cwd=cwd, label=label, options=options)
        except BaseException as exc:
            queue.record_outcome(_classify_exception(exc))
            raise
        else:
            queue.record_outcome("success")
            return result


# ---------------------------------------------------------------------------
# Worktree lease handshake
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeLeaseHolder:
    """The identity handed to :func:`begin_agent_lease`.

    A holder string is written into the lock-file so post-mortem
    inspections (and tests) can see who holds the lease. ``name`` MUST
    be short and printable (a single line, no slashes, no NULs).
    """

    name: str
    acquired_at_unix: float = 0.0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("WorktreeLeaseHolder.name must not be empty")
        if any(ch in self.name for ch in ("\n", "\x00", "/", "\\")):
            raise ValueError(
                f"WorktreeLeaseHolder.name {self.name!r} contains an illegal character"
            )


class WorktreeLeaseUnavailableError(RuntimeError):
    """Raised when an exclusive lease could not be acquired.

    Background mutators should never see this exception — they use
    :func:`try_acquire_lease` (which returns ``None`` on failure). It
    is only surfaced to :func:`begin_agent_phase` callers when the
    agent phase must NOT proceed without an exclusive lease.
    """


#: Backwards-compatible alias kept because the plan's API names the
#: exception ``WorktreeLeaseUnavailable``; both names refer to one class.
WorktreeLeaseUnavailable = WorktreeLeaseUnavailableError


class WorktreeLease:
    """Context manager granted by :func:`begin_agent_lease`.

    The lease owns the lock-file at
    ``<repo>/.git/ralph/agent-lease.lock``. ``__exit__`` closes the
    fd, removes the lock-file, and clears the cached handle so a
    follow-up ``begin_agent_lease`` call can succeed immediately.

    The lease is NOT thread-aware: ``__exit__`` from one thread while
    another holds the same Python descriptor is undefined. The current
    production use is always single-agent-phase per repo, so the
    concurrency model is "one phase, one thread".
    """

    def __init__(self, lock_path: Path, holder: WorktreeLeaseHolder) -> None:
        self._lock_path = lock_path
        self._holder = holder
        self._fd: int | None = None
        self._released: bool = False

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    @property
    def holder(self) -> WorktreeLeaseHolder:
        return self._holder

    def release(self) -> None:
        """Drop the lease and remove the lock-file.

        Idempotent: calling twice is safe. Safe to call from
        ``__exit__`` and from an explicit ``end_agent_lease``. The
        underlying unlink is wrapped in ``contextlib.suppress(OSError)``
        so a crash-recovered lock-file (already removed by another
        process) does not error the caller.
        """
        if self._released:
            return
        self._released = True
        if self._fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None
        with contextlib.suppress(OSError):
            self._lock_path.unlink()

    def __enter__(self) -> WorktreeLease:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()


def _lease_lock_path(repo: Repo) -> Path:
    git_dir = Path(repo.git_dir)
    return git_dir / "ralph" / _AGENT_LEASE_FILENAME


#: Separator count in ``<holder>|<timestamp>`` lock-file lines; a parsed
#: line must yield at least this many ``|``-split fields.
_LEASE_LINE_MIN_FIELDS: int = 2


def _read_lease_holder(lock_path: Path) -> WorktreeLeaseHolder | None:
    """Return the holder recorded in ``lock_path``, or ``None`` if absent.

    A stale lease (older than ``_LEASE_STALE_SECONDS``) is reported as
    ``None`` so the caller can reclaim it. A corrupted lock-file
    (unparseable) is also reported as ``None`` to fail open: rather
    than refuse to acquire, we let the new caller win, which matches
    the "background mutator skip" semantics — a broken lease is a
    weaker blocker than a held one.
    """
    try:
        raw = lock_path.read_bytes()
    except FileNotFoundError:
        return None
    if not raw:
        return None
    first_line = raw.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    parts = first_line.split("|", 1)
    if len(parts) < _LEASE_LINE_MIN_FIELDS or not parts[0]:
        return None
    try:
        acquired_at = float(parts[1])
    except ValueError:
        return None
    if (time.monotonic() - acquired_at) > _LEASE_STALE_SECONDS:
        return None
    return WorktreeLeaseHolder(name=parts[0])


def _acquire_lock_file(lock_path: Path, holder: WorktreeLeaseHolder) -> int:
    """Create ``lock_path`` with ``O_CREAT|O_EXCL``; return the open fd.

    On success the caller holds an open fd for the lifetime of the
    lease (closing it does not release the lock — ``O_EXCL`` unbinds
    on unlink only; ``fcntl.flock`` would be cleaner but POSIX-only).
    A missing parent directory is created; on creation failure the
    native OSError is propagated.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{holder.name}|{time.monotonic():.6f}\n".encode()
    fd = os.open(
        str(lock_path),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC,
        0o600,
    )
    try:
        written = os.write(fd, payload)
    except OSError:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            lock_path.unlink()
        raise
    if written != len(payload):
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            lock_path.unlink()
        msg = f"short write to lease file {lock_path!r}"
        raise OSError(errno.EIO, msg, str(lock_path))
    return fd


def _steal_stale_lease(
    lock_path: Path,
    holder: WorktreeLeaseHolder,
) -> int | None:
    """Reclaim a lease whose recorded holder is stale (or unparseable).

    Returns the new fd on successful steal; ``None`` if a competing
    process stole first and the file is now held fresh.
    """
    current = _read_lease_holder(lock_path)
    if current is not None:
        return None
    try:
        return _acquire_lock_file(lock_path, holder)
    except FileExistsError:
        return None


def begin_agent_lease(
    repo_or_path: Repo | Path | str,
    *,
    holder: WorktreeLeaseHolder | None = None,
) -> WorktreeLease:
    """Acquire the worktree lease exclusively; raise on contention.

    Blocks-then-succeeds semantics: this function only returns when
    the lease is held by the calling process. On contention from
    another live holder it raises
    :class:`WorktreeLeaseUnavailable`. On stale-leftover (a crashed
    prior holder whose lock-file is older than
    ``_LEASE_STALE_SECONDS``) it reclaims.

    Args:
        repo_or_path: A :class:`git.Repo`, a :class:`Path`, or a
            string identifying the repository. The lease lock-file is
            placed under ``<common-git-dir>/ralph/agent-lease.lock``,
            so callers using either the main checkout or any of its
            linked worktrees collide on the same lock.
        holder: Optional :class:`WorktreeLeaseHolder`. When omitted,
            a placeholder holder is auto-generated from the calling
            thread (tests and CLI entry points use this). Production
            callers pass an explicit holder so the lock-file is
            self-describing.

    Returns:
        A :class:`WorktreeLease` context manager. Pass it to
        :func:`end_agent_lease` or use ``with`` to release
        automatically.

    Raises:
        WorktreeLeaseUnavailable: Another live holder owns the lease.
        OSError: Filesystem-level failure (permission, disk full).
    """
    repo = _repo_or_open(repo_or_path)
    lock_path = _lease_lock_path(repo)
    effective_holder = holder or WorktreeLeaseHolder(name="agent-phase")
    fd: int | None = None
    try:
        fd = _acquire_lock_file(lock_path, effective_holder)
    except FileExistsError as exc:
        stolen = _steal_stale_lease(lock_path, effective_holder)
        if stolen is None:
            raise WorktreeLeaseUnavailable(
                f"worktree lease for {lock_path} is held by another agent"
            ) from exc
        fd = stolen
    lease = WorktreeLease(lock_path, effective_holder)
    lease._fd = fd
    return lease


def try_acquire_lease(
    repo_or_path: Repo | Path | str,
    *,
    holder: WorktreeLeaseHolder | None = None,
) -> WorktreeLease | None:
    """Acquire the lease if available; else return ``None``.

    The non-throwing variant of :func:`begin_agent_lease`. Used by
    background mutators (the auto-integration catch-up ticker) that
    must skip a tick when an agent phase is in progress.

    Returns:
        The :class:`WorktreeLease` on success; ``None`` when another
        live holder owns it.
    """
    try:
        return begin_agent_lease(repo_or_path, holder=holder)
    except WorktreeLeaseUnavailable:
        return None


def end_agent_lease(lease: WorktreeLease) -> None:
    """Release a previously-acquired lease.

    Idempotent: a second call on a released lease is a no-op. Always
    safe to call in a ``finally`` block.
    """
    lease.release()


def is_lease_held(repo_or_path: Repo | Path | str) -> bool:
    """Return True when a live lease exists for ``repo_or_path``.

    Differs from :func:`try_acquire_lease` in that it does NOT attempt
    to steal. A stale lease is reported as not-held only when it
    would be reclaimable (i.e. the recorded holder has exceeded
    ``_LEASE_STALE_SECONDS``). Background mutators use this to decide
    whether to skip a tick.
    """
    repo = _repo_or_open(repo_or_path)
    lock_path = _lease_lock_path(repo)
    return _read_lease_holder(lock_path) is not None


def _repo_or_open(repo_or_path: Repo | Path | str) -> Repo:
    """Return ``repo_or_path`` if it already is a ``Repo``; else open it."""
    if isinstance(repo_or_path, Repo):  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        return repo_or_path
    return Repo(Path(repo_or_path))


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


__all__ = [
    "GitCommandQueue",
    "QueuedCallOutcome",
    "WorktreeLease",
    "WorktreeLeaseHolder",
    "WorktreeLeaseUnavailable",
    "WorktreeLeaseUnavailableError",
    "begin_agent_lease",
    "end_agent_lease",
    "get_queue_for_repo",
    "is_lease_held",
    "reset_all_queues",
    "route_git_method",
    "route_run_git",
    "try_acquire_lease",
]
