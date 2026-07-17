"""Workspace monitoring for file changes during agent execution."""

from __future__ import annotations

import importlib
import inspect
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from loguru import logger

from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.agents.invoke._has_src_path import _HasSrcPath
from ralph.agents.invoke._workspace_change_classifier import (
    ARTIFACT_PARENT_DIRS,
    CACHE_PARENT_DIRS,
    WorkspaceChangeClassifier,
)

if TYPE_CHECKING:

    class _HasStop(Protocol):
        """Protocol for watchdog Observer-like objects that have a stop method."""

        def stop(self) -> None: ...
        def join(self, _timeout: float | None = None) -> None: ...

    class _ObserverProtocol(_HasStop, Protocol):
        """Protocol for watchdog Observer-like objects used by this module."""

        def schedule(self, _event_handler: object, path: str, **_kwargs: object) -> None: ...
        def start(self) -> None: ...

    class _WatchdogObserversModule(Protocol):
        """Typed accessor for the optional watchdog.observers module."""

        Observer: type[_ObserverProtocol]


_MAX_WORKSPACE_CHANGED_FILES = 512
_VALID_CALLBACK_ARITIES: frozenset[int] = frozenset({0, 2})
_TWO_ARG_ARITY: int = 2


#: Union of the two valid on_event callback signatures. A callback
#: with no required positional args (the legacy 0-arg binding) is
#: accepted for backward compatibility; a callback with exactly 2
#: required positional args (the production 2-arg binding) carries
#: ``(kind, weight)`` so the watchdog's per-kind counter receives
#: real classifications. The ``__post_init__`` arity check rejects
#: any other arity at construction time.
WorkspaceEventCallback = Callable[[], None] | Callable[[WorkspaceChangeKind, float], None]


#: Union of every zero-weight parent-dir the ACTIVE classifier can
#: drop, imported from the canonical classifier module so the watcher
#: and the classifier can never drift. A parent-dir subtree is
#: excluded from the observer ONLY when the active classifier drops
#: a representative descendant (see ``_watch_exclusions``); this set
#: is the probe population, not the exclusion result.
_WATCH_EXCLUSION_CANDIDATES: frozenset[str] = CACHE_PARENT_DIRS | ARTIFACT_PARENT_DIRS


#: Cap on the bounded catch-up rescan so a huge moved-in tree cannot
#: block the watchdog dispatch thread. The cap is a soft ceiling;
#: ``record_event`` remains the correctness backstop for anything
#: beyond the cap.
_MAX_CATCHUP_FILES: int = 4096


@runtime_checkable
class _DirEventFields(Protocol):
    """Protocol for watchdog events that expose directory-arrival fields.

    Used by ``WorkspaceMonitor.maybe_watch_new_directory`` to duck-type
    watchdog ``DirCreatedEvent`` / ``DirMovedEvent`` without importing
    watchdog at runtime.
    """

    is_directory: bool
    event_type: str
    src_path: str


@runtime_checkable
class _HasDestPath(Protocol):
    """Protocol for watchdog events that expose a destination path.

    Used to detect ``DirMovedEvent`` so a directory MOVED into the
    workspace can be watched on its ``dest_path`` (the in-workspace
    arrival point) instead of its ``src_path`` (which is outside the
    workspace).
    """

    dest_path: str


class _HandlerWithDispatch(Protocol):
    """Structural type of the per-monitor watchdog handler.

    ``_make_change_tracker`` returns a class with a public
    ``dispatch(event)`` method; ``WorkspaceMonitor.dispatch_event``
    routes test-supplied events through that method. Defined as a
    Protocol so the ``cast`` in ``dispatch_event`` does not need an
    ``attr-defined`` suppression (test files must have zero
    suppressions).
    """

    def dispatch(self, event: object) -> None: ...


def _watch_exclusions(classifier: WorkspaceChangeClassifier | None) -> frozenset[str]:
    """Derive the watch-exclusion set from the ACTIVE classifier.

    The set is empty when ``classifier`` is ``None`` (legacy: every
    event counts, so nothing may be excluded -- preserves the single
    recursive-root watch). Otherwise each candidate root from
    ``_WATCH_EXCLUSION_CANDIDATES`` is probed with a representative
    descendant path; if the active classifier drops the descendant
    (weight ``0.0``), the root is added to the exclusion set.

    Correctness invariant: the classifier's rule order makes every
    descendant of a ``CACHE_PARENT_DIRS`` entry uniformly ``CACHE``
    (rule 1) and every descendant of an ``ARTIFACT_PARENT_DIRS`` entry
    uniformly ``ARTIFACT`` (rule 4), so a single probe per root is
    representative of the entire subtree. Pruning a dropped subtree
    is therefore behavior-neutral: ``record_event`` still applies its
    classify-drop backstop for any event that somehow leaks through.
    """
    if classifier is None:
        return frozenset()
    excluded: set[str] = set()
    for candidate in _WATCH_EXCLUSION_CANDIDATES:
        probe = f"/__ralph_watch_probe__/{candidate}/__probe__"
        _kind, weight = classifier.classify(probe)
        if weight == 0.0:
            excluded.add(candidate)
    return frozenset(excluded)


def _is_within_excluded(rel: str, exclusions: frozenset[str]) -> bool:
    """Return True iff ``rel`` is inside an excluded subtree.

    A relative path is "within excluded" iff:

    - it equals an exclusion exactly (e.g. ``.git``), OR
    - it is a descendant of an exclusion (e.g. ``.git/objects``
      because its prefix ``.git/`` matches an exclusion).
    """
    if rel in exclusions:
        return True
    return any(rel.startswith(exclusion + "/") for exclusion in exclusions)


def _has_exclusion_descendant(rel: str, exclusions: frozenset[str]) -> bool:
    """Return True iff some exclusion is a strict descendant of ``rel``.

    Used by ``build_watch_plan`` to decide whether to emit a
    non-recursive watch (then recurse into non-excluded children)
    versus a single recursive watch (then stop descending).
    """
    if rel == "":
        return len(exclusions) > 0
    prefix = rel + "/"
    return any(exclusion.startswith(prefix) for exclusion in exclusions)


def build_watch_plan(
    root: str,
    list_subdirs: Callable[[str], list[str]],
    exclusions: frozenset[str],
) -> list[tuple[str, bool]]:
    """Pruned walk that emits scoped watchdog watches.

    For each visited ``rel`` (POSIX-relative to ``root``; ``""`` for
    the root itself):

    1. If ``rel`` is excluded or has an excluded ancestor, prune
       (do not emit; do not descend).
    2. If NO exclusion is a descendant of ``rel``, emit
       ``(rel, True)`` and stop descending -- a recursive watch
       covers the whole subtree in one stream.
    3. Otherwise emit ``(rel, False)`` and recurse into each
       non-excluded immediate child (covered by the injected
       ``list_subdirs``).

    When ``exclusions`` is empty, the result is exactly ``[('', True)]``
    (a single recursive root watch, byte-identical to today's
    behavior). The returned list preserves a deterministic order
    (root, then depth-first per child) so tests can assert equality
    on the plan shape.
    """
    plan: list[tuple[str, bool]] = []

    def _walk(rel: str) -> None:
        if _is_within_excluded(rel, exclusions):
            return
        if not _has_exclusion_descendant(rel, exclusions):
            plan.append((rel, True))
            return
        plan.append((rel, False))
        abs_dir = str(Path(root) / rel) if rel else root
        for child_name in list_subdirs(abs_dir):
            child_rel = f"{rel}/{child_name}" if rel else child_name
            _walk(child_rel)

    _walk("")
    return plan


def _scandir_dirs(abs_dir: str) -> list[str]:
    """Production ``list_subdirs`` implementation using ``os.scandir``.

    Returns the immediate child directory names under ``abs_dir``
    without following symlinks. ``OSError`` is swallowed and yields
    an empty list so a transient enumeration failure never breaks
    the scheduling path.
    """
    try:
        with os.scandir(abs_dir) as it:
            return [
                entry.name
                for entry in it
                if entry.is_dir(follow_symlinks=False)
            ]
    except OSError:
        return []


def _scandir_tree_files(abs_dir: str) -> list[str]:
    """Production ``list_tree_files`` implementation using ``os.walk``.

    Walks the subtree rooted at ``abs_dir``, pruning single-part
    excluded directory names (``_WATCH_EXCLUSION_CANDIDATES`` whose
    name has no ``/``) so a vendored ``node_modules`` or ``.git``
    subtree does not slow down the catch-up. Returns up to
    ``_MAX_CATCHUP_FILES`` file paths; ``record_event``'s classify-drop
    remains the correctness backstop for anything beyond the cap.
    ``OSError`` is swallowed and yields an empty list.
    """
    single_part_excluded: frozenset[str] = frozenset(
        candidate for candidate in _WATCH_EXCLUSION_CANDIDATES if "/" not in candidate
    )
    result: list[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(abs_dir, topdown=True):
            dirnames[:] = [
                name for name in dirnames if name not in single_part_excluded
            ]
            for filename in filenames:
                if len(result) >= _MAX_CATCHUP_FILES:
                    return result
                result.append(str(Path(dirpath) / filename))
    except OSError:
        return []
    return result


def _dir_arrival_candidate(event: object) -> str | None:
    """Extract the in-workspace path candidate from a directory event.

    Returns the ``src_path`` for an ``event_type='created'`` event
    or the ``dest_path`` for an ``event_type='moved'`` event (when
    the event exposes a ``dest_path``). Returns ``None`` for any
    event that is not a directory event we should act on (non-dir,
    unknown event_type, missing destination).
    """
    if not isinstance(event, _DirEventFields):
        return None
    if not event.is_directory:
        return None
    if event.event_type == "created":
        return event.src_path
    if event.event_type == "moved" and isinstance(event, _HasDestPath):
        return event.dest_path
    return None


def _make_change_tracker(monitor: WorkspaceMonitor) -> object:
    class _ChangeTrackerHandler:
        def dispatch(self, event: object) -> None:
            self.on_any_event(event)

        def on_any_event(self, event: object) -> None:
            if isinstance(event, _HasSrcPath):
                monitor.record_event(event.src_path)
            monitor.maybe_watch_new_directory(event)

    return _ChangeTrackerHandler()


def _create_watchdog_observer() -> _ObserverProtocol | None:
    """Construct a watchdog observer when the optional dependency is installed."""
    try:
        observers_module = cast(
            "_WatchdogObserversModule",
            importlib.import_module("watchdog.observers"),
        )
    except ImportError:
        return None
    return observers_module.Observer()


def _callback_arity(callback: WorkspaceEventCallback) -> int:
    """Return the number of required positional parameters of ``callback``.

    Used by ``WorkspaceMonitor.__post_init__`` to enforce the 0-arg
    or 2-arg contract. ``inspect.signature`` already follows
    ``functools.partial`` and ``functools.wraps`` chains, and
    automatically excludes the bound ``self`` parameter for bound
    methods, so the returned ``signature.parameters`` map is
    authoritative for the effective positional arity. A callback
    with ``*args`` or ``**kwargs`` has a non-finite arity; the
    classifier counts the explicit positional slots before ``*args``
    and treats the result as the effective arity.

    Returns:
        Number of required positional parameters as observed by the
        caller (excluding ``self`` for bound methods).
    """
    try:
        signature_obj: inspect.Signature = inspect.signature(callback)
    except (TypeError, ValueError):
        msg = (
            f"WorkspaceMonitor on_event callback has an uninspectable signature;"
            f" expected 0 or 2 required positional args, got callback of type"
            f" {type(callback).__name__}"
        )
        raise ValueError(msg) from None
    can_bind_zero = _can_bind_n(signature_obj, 0)
    can_bind_two = _can_bind_n(signature_obj, 2)
    if can_bind_zero and not can_bind_two:
        return 0
    if can_bind_two and not can_bind_zero:
        return 2
    msg = (
        f"WorkspaceMonitor on_event callback has the wrong arity;"
        f" expected exactly 0 or 2 required positional args, got"
        f" callback of type {type(callback).__name__}"
    )
    raise ValueError(msg)


def _can_bind_n(signature_obj: inspect.Signature, n: int) -> bool:
    """Return True iff ``signature_obj`` accepts exactly ``n`` positional args.

    A variadic-only signature (e.g. ``*args, **kwargs``) accepts any
    number of args, so both ``n=0`` and ``n=2`` return True. The
    arity check in ``_callback_arity`` rejects signatures where
    both bind successfully, so a variadic-only callback is not
    mistakenly classified as 0-arg or 2-arg.

    Used to avoid touching ``Parameter.kind`` (which is typed as
    ``Any`` in the upstream typeshed stub) and the
    ``Parameter.empty`` sentinel (also ``Any``-typed) so the
    mypy ``disallow_any_expr`` check does not flag the
    ``inspect.Parameter``-typed expressions.
    """
    args: tuple[object, ...] = tuple(object() for _ in range(n))
    try:
        signature_obj.bind(*args)
    except TypeError:
        return False
    return True


class WorkspaceMonitor:
    """Monitors workspace directory for file changes during agent execution.

    This allows the pipeline to detect when an agent has completed significant
    work by watching for file modifications in the workspace.
    """

    def __init__(
        self,
        workspace_path: Path,
        *,
        now: Callable[[], float] | None = None,
        on_event: WorkspaceEventCallback | None = None,
        classifier: WorkspaceChangeClassifier | None = None,
        list_subdirs: Callable[[str], list[str]] | None = None,
        list_tree_files: Callable[[str], list[str]] | None = None,
    ) -> None:
        """Initialize workspace monitor.

        Args:
            workspace_path: Path to the workspace directory to monitor.
            now: Optional monotonic-clock callable. Defaults to ``time.monotonic``
                for production. Tests inject a FakeClock-bound callable to drive
                ``last_event_at`` deterministically (see tests in
                tests/agents/test_idle_watchdog_3.py::test_workspace_monitor_records_last_event_at).
            on_event: Optional callable invoked at the end of ``record_event``
                after the timestamp and counter are updated. Production readers
                bind this to ``watchdog.record_workspace_event`` (via
                ``set_on_event`` after the watchdog is constructed) so the
                activity-aware verdict can defer ``NO_OUTPUT_DEADLINE`` while
                the workspace is changing. Exceptions raised by ``on_event`` are
                swallowed so a buggy callback cannot break the file-event path.

                Two arities are accepted: the legacy 0-arg form (the
                watchdog's ``record_workspace_event`` bound method, which
                receives only ``now`` via the watchdog's clock) and the
                production 2-arg form (a small lambda that forwards
                ``(kind, weight)`` so the watchdog's per-kind counter
                receives real classifications). A 1-arg or 3+-arg callback
                is rejected at construction time with a clear ValueError
                naming the offending arity.

                The callback is invoked in a ``try/except`` so a buggy
                callback cannot break the file-event path; the failure
                is logged at DEBUG.
            classifier: Optional ``WorkspaceChangeClassifier`` used to
                classify each event into a ``WorkspaceChangeKind`` and a
                binary weight. When omitted (or ``None``), every event
                is classified as ``OTHER`` with weight ``1.0`` (the legacy
                behavior: every file change counts as activity). When
                provided, events with weight ``0.0`` are dropped
                before ``on_event`` is invoked: the timestamp and
                counter are NOT updated and the callback is NOT
                invoked. Events with weight ``1.0`` are passed to the
                callback together with their ``(kind, weight)`` tuple
                when the callback accepts 2 args.
            list_subdirs: Optional ``Callable[[str], list[str]]`` that
                returns the immediate child directory names under an
                absolute directory path. Production uses
                ``_scandir_dirs`` (``os.scandir`` based, no symlink
                following, ``OSError`` swallowed); tests inject an
                in-memory tree so no real filesystem is touched. Used
                by ``start()`` to build the classifier-scoped watch
                plan via ``build_watch_plan``.
            list_tree_files: Optional ``Callable[[str], list[str]]``
                that returns the file paths under an absolute
                directory (a bounded, depth-first walk). Production
                uses ``_scandir_tree_files`` (``os.walk`` based,
                pruned on single-part excluded dir names, capped at
                ``_MAX_CATCHUP_FILES``); tests inject a static map.
                Used by ``maybe_watch_new_directory`` for the catch-up
                rescan so pre-watch descendant source files are not
                lost when a directory arrives after ``start()``.
        """
        self._workspace = workspace_path
        self._observer: _ObserverProtocol | None = None
        self._event_count = 0
        self._seen_files: dict[str, None] = {}  # bounded-accumulator-ok: bounded
        self._now: Callable[[], float] = now if now is not None else time.monotonic
        self._last_event_at: float | None = None
        if on_event is not None:
            arity = _callback_arity(on_event)
            if arity not in _VALID_CALLBACK_ARITIES:
                msg = (
                    f"WorkspaceMonitor on_event callback has arity {arity};"
                    f" expected 0 (legacy) or 2 (production-style forwarding of"
                    f" (kind, weight))."
                )
                raise ValueError(msg)
        self._on_event: WorkspaceEventCallback | None = on_event
        self._classifier: WorkspaceChangeClassifier | None = classifier
        # Watch-scoping state (AC-01..AC-05). The exclusion set is
        # derived from the ACTIVE classifier at construction time so
        # a configuration change to ``weights`` (operator opt-in for
        # cache=1.0 or artifact=1.0) is reflected in the next
        # ``start()``; the set is fixed for the lifetime of this
        # monitor instance.
        self._watch_exclusions: frozenset[str] = _watch_exclusions(classifier)
        self._list_subdirs: Callable[[str], list[str]] = (
            list_subdirs if list_subdirs is not None else _scandir_dirs
        )
        self._list_tree_files: Callable[[str], list[str]] = (
            list_tree_files if list_tree_files is not None else _scandir_tree_files
        )
        # Tracks the workspace-relative paths already handed to the
        # observer so dynamic re-scheduling is idempotent (a duplicate
        # created/moved-in event for the same path does NOT produce a
        # second ``observer.schedule`` call). Cleared in ``stop()``.
        self._scheduled_watch_paths: set[str] = set()  # bounded-accumulator-ok: bounded by workspace directory count; cleared in stop()
        self._watch_lock: threading.Lock = threading.Lock()
        self._handler: object | None = None

    def start(self) -> None:
        """Start monitoring the workspace for file changes.

        Schedules watchdog watches per ``build_watch_plan`` derived
        from the ACTIVE classifier's exclusion set (AC-01). When the
        exclusion set is empty (``classifier=None``), a single
        recursive root watch is scheduled -- byte-identical to
        today's behavior. Otherwise a pruned walk emits one recursive
        watch per clean subtree (e.g. ``src/``) and non-recursive
        watches on ancestors of excluded roots (e.g. ``.agent`` so
        ``.agent/PLAN.md`` events still arrive but ``.agent/tmp`` /
        ``.agent/raw`` / ``.agent/artifacts`` events do not).

        The plan-driven scheduling is the fseventsd-scope reduction:
        macOS fseventsd stops servicing and delivering events under
        excluded subtrees because watchdog never subscribes to them
        in the first place. ``record_event`` retains its
        ``weight == 0.0`` classify-drop backstop so any event that
        somehow leaks through is still dropped at the observer
        boundary.
        """
        observer = _create_watchdog_observer()
        if observer is None:
            return

        handler = _make_change_tracker(self)
        workspace_str = str(self._workspace)
        self._observer = observer
        self._handler = handler
        plan = build_watch_plan(workspace_str, self._list_subdirs, self._watch_exclusions)
        with self._watch_lock:
            self._scheduled_watch_paths.clear()
            for rel, recursive in plan:
                abs_path = str(Path(workspace_str) / rel) if rel else workspace_str
                self._observer.schedule(handler, abs_path, recursive=recursive)
                self._scheduled_watch_paths.add(rel)
        self._observer.start()
        logger.debug("Started workspace monitoring: {}", self._workspace)

    def record_event(self, src_path: str) -> None:
        """Record a file change event.

        Classifies the event via the configured ``WorkspaceChangeClassifier``
        (or the legacy ``OTHER / 1.0`` fallback when no classifier is
        configured). Events with weight ``0.0`` are dropped without
        updating ``last_event_at``, the counter, or invoking
        ``on_event``. Events with weight ``1.0`` update the timestamp
        and counter and invoke ``on_event`` with the ``(kind, weight)``
        pair when the callback accepts 2 args.

        The watchdog's per-channel evidence surface consumes this
        timestamp via the ``last_workspace_event_at`` field on
        ``CorroborationSnapshot`` so a workspace-event channel is
        fresh exactly as long as the production clock is recent.

        When an ``on_event`` callback has been registered (via the
        constructor or ``set_on_event``), it is invoked AFTER the
        timestamp and counter are updated so the watchdog observes a
        fully-consistent state. The callback is invoked in a
        ``try/except`` so a buggy callback cannot break the
        file-event path; the failure is logged at DEBUG.

        Args:
            src_path: Path to the changed file.
        """
        kind, weight = self.classify_path(src_path)
        if weight == 0.0:
            return
        self._seen_files.pop(src_path, None)
        self._seen_files[src_path] = None
        while len(self._seen_files) > _MAX_WORKSPACE_CHANGED_FILES:
            oldest = next(iter(self._seen_files))
            del self._seen_files[oldest]
        self._event_count += 1
        self._last_event_at = self._now()
        if self._on_event is not None:
            try:
                arity = _callback_arity(self._on_event)
                if arity == _TWO_ARG_ARITY:
                    two_arg = cast(
                        "Callable[[WorkspaceChangeKind, float], None]",
                        self._on_event,
                    )
                    two_arg(kind, weight)
                else:
                    zero_arg = cast("Callable[[], None]", self._on_event)
                    zero_arg()
            except Exception:
                logger.opt(exception=True).debug(
                    "workspace monitor: on_event callback raised (suppressed)"
                )

    def classify_path(self, src_path: str) -> tuple[WorkspaceChangeKind, float]:
        """Classify a single workspace path via the configured classifier.

        When no classifier is configured, every path is classified as
        ``OTHER`` with weight ``1.0`` (the legacy behavior: every
        file change counts as activity). This helper is the
        canonical seam for tests and dry-run checks that want to
        inspect the classifier output without recording an event.
        """
        if self._classifier is None:
            return WorkspaceChangeKind.OTHER, 1.0
        return self._classifier.classify(src_path)

    def stop(self) -> None:
        """Stop monitoring the workspace."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(5)
            self._observer = None
            logger.debug(
                "Stopped workspace monitoring: {} ({} events)",
                self._workspace,
                self._event_count,
            )
        with self._watch_lock:
            self._scheduled_watch_paths.clear()
            self._handler = None

    def maybe_watch_new_directory(self, event: object) -> None:
        """Schedule a recursive watch on a directory that just arrived.

        Handles two watchdog event shapes:

        - ``is_directory=True`` + ``event_type='created'`` -> the
          ``src_path`` is the in-workspace path of the new directory.
        - ``is_directory=True`` + ``event_type='moved'`` AND the
          event exposes ``dest_path`` -> the ``dest_path`` is the
          in-workspace arrival point (PA-002). The ``src_path`` is
          outside the workspace for a moved-in dir.

        The candidate is rejected if:

        - it is not a directory event;
        - the resolved workspace-relative path starts with ``..`` or
          is absolute (out-of-workspace, e.g. ``dest_path='/elsewhere/x'``);
        - it equals an exclusion or sits inside an excluded subtree
          (e.g. ``/ws/.git/objects/aa``).

        On success the method schedules a recursive watchdog watch
        on the in-workspace path (idempotent -- a duplicate event for
        the same path produces a single ``observer.schedule`` call)
        AND runs a bounded catch-up rescan so any pre-watch descendant
        source file is fed through ``record_event`` (the
        ``classify-drop`` backstop ensures non-activity stays
        dropped). The whole body is wrapped in a try/except so a
        scheduling failure (a watchdog backend race, an OSError on
        the catch-up rescan, ...) never breaks the file-event path.

        Args:
            event: A watchdog-style event object. Duck-typed via the
                ``_DirEventFields`` / ``_HasDestPath`` protocols so
                no watchdog type is imported at module level.
        """
        try:
            rel = self._extract_dir_arrival_rel(event)
            if rel is None:
                return
            abs_path = self._schedule_new_dir_watch(rel)
            if abs_path is None:
                return
            # Catch-up rescan OUTSIDE the lock so the watchdog dispatch
            # thread is not blocked longer than necessary. record_event
            # applies the classify-drop backstop for cache/log files
            # so the rescan never re-counts dropped activity.
            for fpath in self._list_tree_files(abs_path):
                self.record_event(fpath)
        except Exception:
            logger.opt(exception=True).debug(
                "workspace monitor: dynamic watch scheduling failed (suppressed)"
            )

    def _extract_dir_arrival_rel(self, event: object) -> str | None:
        """Validate the event and return the workspace-relative arrival path.

        Returns ``None`` when the event is not a directory arrival
        we can act on (not a directory event, unknown event_type,
        destination outside the workspace, or inside an excluded
        subtree). The workspace-relative path uses POSIX separators
        so it can be matched against ``_WATCH_EXCLUSION_CANDIDATES``
        (which are POSIX) without an os-specific translation.
        """
        candidate = _dir_arrival_candidate(event)
        if candidate is None:
            return None
        workspace_str = str(self._workspace)
        try:
            rel = os.path.relpath(candidate, workspace_str)
        except ValueError:
            # Different drives on Windows -- treat as out-of-workspace.
            return None
        if Path(rel).is_absolute():
            return None
        # Reject a true parent traversal only -- ``rel == ".."`` or
        # ``rel.startswith("../")`` (POSIX normalized). Legal directory
        # names whose basename starts with two dots (e.g. ``..keep``,
        # which ``os.path.relpath`` returns as ``..keep``) must NOT be
        # treated as out-of-workspace; ``startswith("..")`` would
        # wrongly reject them. See
        # ``test_created_directory_with_dotdot_prefixed_name_schedules_recursive_watch``.
        rel = rel.replace(os.sep, "/")
        if rel == ".." or rel.startswith("../"):
            return None
        if _is_within_excluded(rel, self._watch_exclusions):
            return None
        return rel

    def _schedule_new_dir_watch(self, rel: str) -> str | None:
        """Idempotently schedule a recursive watch on ``rel``.

        Returns the absolute in-workspace path on success (so the
        caller can run the catch-up rescan) or ``None`` when the
        monitor is stopped, ``rel`` is already scheduled, or the
        lock could not be acquired.
        """
        workspace_str = str(self._workspace)
        abs_path = str(Path(workspace_str) / rel)
        with self._watch_lock:
            if self._observer is None or self._handler is None:
                return None
            if rel in self._scheduled_watch_paths:
                return None
            self._observer.schedule(self._handler, abs_path, recursive=True)
            self._scheduled_watch_paths.add(rel)
        return abs_path

    def dispatch_event(self, event: object) -> None:
        """Dispatch a watchdog-style event through the per-monitor handler.

        Exposed for black-box tests that drive the handler directly
        with a synthetic event (FakeEvent). Production callers do not
        need this -- the watchdog backend dispatches events into the
        handler returned by ``_make_change_tracker(self)`` itself.

        The handler routes every event through ``record_event`` (if it
        has ``src_path``) and ``maybe_watch_new_directory`` (if it
        carries the directory-arrival fields), mirroring the
        production watchdog dispatch path.

        Args:
            event: A watchdog-style event object. The handler
                duck-types via the ``_HasSrcPath`` /
                ``_DirEventFields`` / ``_HasDestPath`` protocols so
                any object with the expected attributes is accepted.
        """
        if self._handler is None:
            return
        handler = cast("_HandlerWithDispatch", self._handler)
        handler.dispatch(event)

    @property
    def event_count(self) -> int:
        """Number of file change events detected."""
        return self._event_count

    @property
    def last_event_at(self) -> float | None:
        """Monotonic-clock timestamp of the most recent file change event.

        Returns None when no event has been observed since the monitor was
        constructed (or since the last ``reset_last_event_at`` call). The
        watchdog's per-channel evidence surface consumes this value via
        the ``last_workspace_event_at`` field on ``CorroborationSnapshot``;
        a fresh workspace channel defers the NO_OUTPUT_DEADLINE verdict
        while the channel age is below ``activity_evidence_ttl_seconds``.
        """
        return self._last_event_at

    def reset_last_event_at(self) -> None:
        """Reset ``last_event_at`` (and the event counter) to a clean state.

        Intended for test isolation: a long-lived ``WorkspaceMonitor`` in
        a test fixture may have observed events from a prior case; calling
        this clears the timestamp so the next ``record_event`` produces a
        fresh baseline.
        """
        self._last_event_at = None
        self._event_count = 0
        self._seen_files.clear()

    def set_on_event(self, on_event: WorkspaceEventCallback | None) -> None:
        """Register (or clear) the per-event callback invoked at the end of
        ``record_event``.

        Production readers construct the ``WorkspaceMonitor`` BEFORE the
        per-run watchdog is created (the monitor is built in
        ``invoke_agent`` while the watchdog lives inside the reader's
        ``read_lines`` generator), so the constructor cannot bind the
        watchdog's ``record_workspace_event`` directly. The reader
        registers the callback here, immediately after the watchdog is
        created, so every subsequent file change is visible to the
        activity-aware verdict as workspace channel evidence.

        Pass ``None`` to clear the callback (e.g. when the per-run
        watchdog is torn down at run end).

        Args:
            on_event: Callable invoked with no arguments at the end of
                ``record_event`` (legacy 0-arg form) or with
                ``(kind, weight)`` (production 2-arg form) after the
                timestamp and counter are updated. Exceptions raised
                by the callback are suppressed by ``record_event``
                so a buggy callback cannot break the file-event path.
        """
        if on_event is not None:
            arity = _callback_arity(on_event)
            if arity not in _VALID_CALLBACK_ARITIES:
                msg = (
                    f"WorkspaceMonitor on_event callback has arity {arity};"
                    f" expected 0 (legacy) or 2 (production-style forwarding of"
                    f" (kind, weight))."
                )
                raise ValueError(msg)
        self._on_event = on_event

    @property
    def changed_files(self) -> set[str]:
        """Set of file paths that changed during monitoring."""
        return set(self._seen_files)
