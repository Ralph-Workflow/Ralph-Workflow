"""Subagent identity contract for the idle watchdog (single source of truth).

This module defines ``SubagentIdentity`` and ``SubagentPidRegistry`` -- the
canonical owner for what ``real subagent`` means in Ralph's watchdog
subsystem. The Trustworthy Idle Watchdog product spec
(``.agent/CURRENT_PROMPT.md``, requirement R1) requires:

    Each monitor MUST count only genuine subagents -- the delegated agent
    work the supervised agent launches. It MUST exclude:

    - the host/supervisor process,
    - internal helper/tooling spawns the agent makes for its own operation,
    - any process that is not a real subagent of the supervised agent.

A process is a real subagent iff (a) it is a live descendant of the supervised
agent PID AND (b) it is REGISTERED in the shared ``SubagentPidRegistry`` by
the transport's authoritative ``SubagentPidSource``. ``psutil.children(recursive=True)``
captures both classes (helpers + real subagents); the registry is the FILTER
that distinguishes them.

The watchdog consumes the FILTERED count exclusively via
``ProcessMonitor.spawned_subagent_count()`` (preferred name; ``live_subagent_count()``
is the legacy alias returning the same value). The broader descendant count
must NEVER be used for the deferral decision -- doing so is the bug cited in
the product spec (the 2365s indefinite deferral of ``CHILDREN_PERSIST_TOO_LONG``
because shell helpers like ``npm test`` were counted as ``children``).

Lifecycle invariants:

    - The registry is bounded at ``_MAX_REGISTRY_ENTRIES`` via FIFO eviction
      (collections.OrderedDict; ``popitem(last=False)`` on overflow).
    - ``register`` is idempotent: duplicate PID calls return the existing
      identity with the FIRST ``registered_at_monotonic`` preserved (no
      timestamp rewrite on retry).
    - ``unregister`` removes a PID; subsequent ``known_pids()`` / ``snapshot()``
      reads do not see it.
    - All public methods are thread-safe (single ``threading.Lock``).

This module is the SINGLE owner for ``SubagentIdentity`` and
``SubagentPidRegistry``. The audit ``subagent_counting_outside_owner``
in ``ralph.testing.audit_watchdog_drift`` enforces the single-owner
invariant so a future PR cannot silently introduce a parallel identity
type without updating this owner.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

_SUBAGENT_SOURCES: frozenset[str] = frozenset(
    {
        "opencode",
        "claude",
        "pi",
        "agy",
        "generic",
        "claude_interactive",
        "codex",
        "nanocoder",
    }
)

# Hard cap on the number of registered subagent identities. The registry is
# the cross-transport authoritative list of "real subagents"; it grows with
# every per-transport discovery call. Unbounded growth would retain
# heavyweight identity records across a long unattended run -- exactly the
# leak class that ``audit_resource_lifecycle`` flags. The cap is high enough
# (1024) to cover the typical 30-50 concurrent subagents observed in long
# running sessions with significant headroom, but bounded so the worst-case
# process-tree-watchdog pair cannot blow up.
_MAX_REGISTRY_ENTRIES: int = 1024


_IdentityMap = OrderedDict[int, "SubagentIdentity"]


@dataclass(frozen=True, slots=True)
class SubagentIdentity:
    """A process that has been SIGNALLY registered by an authoritative source.

    The frozen+slots dataclass invariants match the existing watchdog module
    conventions (see ``IdleWatchdog`` and ``WatchdogFireReason``). All fields
    are required at construction; the registry owns the registration policy
    (FIFO eviction, idempotent register).

    Fields:
        pid: The OS process id of the registered subagent.
        source: The transport that registered the subagent (one of
            ``opencode``, ``claude``, ``pi``, ``agy``, ``generic``,
            ``claude_interactive``, ``codex``, ``nanocoder`` -- the eight
            canonical ``AgentTransport`` source labels).
        registered_at_monotonic: Monotonic timestamp captured when the
            subagent was first registered. On a duplicate ``register`` call
            the original timestamp is preserved (idempotent).
        label_prefix: Optional human-readable label fragment (e.g. the
            ``ChildLivenessRegistry`` worker prefix) surfaced in the
            diagnostic block. ``None`` when the registering source has no
            label.
    """

    pid: int
    source: Literal[
        "opencode",
        "claude",
        "pi",
        "agy",
        "generic",
        "claude_interactive",
        "codex",
        "nanocoder",
    ]
    registered_at_monotonic: float
    label_prefix: str | None = None

    def __post_init__(self) -> None:
        if self.source not in _SUBAGENT_SOURCES:
            msg = (
                f"unknown subagent source {self.source!r}; expected one of"
                f" {sorted(_SUBAGENT_SOURCES)}"
            )
            raise ValueError(msg)
        if self.pid <= 0:
            msg = f"pid must be positive (got {self.pid})"
            raise ValueError(msg)


class SubagentPidRegistry:
    """Thread-safe bounded registry of known subagent identities.

    Single owner of the canonical "real subagent" list. The watchdog
    deferral decision reads ``len(registry.snapshot())`` (or via the
    injected ``SubagentPidSource`` adapter) and ONLY defers when this
    count is > 0. A descendant PID in ``psutil.children(recursive=True)``
    that is NOT in this registry is an ``INCIDENTAL_HELPER`` and does
    NOT block the hard ceiling.

    Concurrency:
        A single ``threading.Lock`` guards every mutation and every
        snapshot read. The registry is intended for use from the
        watchdog evaluate path (single writer) and per-transport
        parser threads (concurrent register/unregister); the lock is
        held only for the brief list/set snapshot, never across I/O.

    Bounds:
        ``_MAX_REGISTRY_ENTRIES = 1024`` entries. FIFO eviction via
        ``OrderedDict.popitem(last=False)`` so the OLDEST-registered
        identity is the first to be dropped when the cap binds. This
        is annotated ``# bounded-accumulator-ok`` for the resource
        lifecycle audit.

    Failure modes:
        Duplicate ``register`` calls for the same PID are idempotent:
        the FIRST ``registered_at_monotonic`` is preserved. ``unregister``
        of an unknown PID is a no-op (returns ``None``). ``snapshot()``
        returns a stable tuple, never a live mutable view.
    """

    __slots__ = ("_identities", "_known_pids_cache", "_lock")

    def __init__(self) -> None:
        # FIFO cap at 1024 entries (collections.OrderedDict
        # ``popitem(last=False)`` eviction on overflow; see
        # ``_MAX_REGISTRY_ENTRIES`` and ``register()`` for the eviction
        # path; the audit ``audit_resource_lifecycle`` enforces the cap).
        self._identities: _IdentityMap = OrderedDict()  # bounded-accumulator-ok: FIFO 1024
        self._lock = threading.Lock()
        # Cache of known PIDs (frozenset) cleared on every mutation; this
        # lets ``known_pids()`` return a stable snapshot without re-walking
        # the OrderedDict on every call. The cache is private -- callers
        # must NOT mutate the returned frozenset (it is immutable by type
        # but the cache is a private invariant).
        self._known_pids_cache: frozenset[int] | None = None

    def register(
        self,
        pid: int,
        source: Literal[
            "opencode",
            "claude",
            "pi",
            "agy",
            "generic",
            "claude_interactive",
            "codex",
            "nanocoder",
        ],
        label_prefix: str | None = None,
        *,
        now: float | None = None,
    ) -> SubagentIdentity:
        """Register a PID as a real subagent for ``source``.

        Idempotent on PID: a duplicate call returns the existing identity
        with the FIRST ``registered_at_monotonic`` preserved (no timestamp
        rewrite on retry). Evicts the oldest entry FIFO when the registry
        hits ``_MAX_REGISTRY_ENTRIES``.

        Args:
            pid: The OS process id of the subagent. Must be > 0.
            source: The transport registering the subagent. Must be one of
                the supported source labels.
            label_prefix: Optional human-readable label fragment.
            now: Monotonic timestamp override for deterministic tests; when
                ``None`` the registry does NOT synthesize a timestamp (the
                caller is expected to inject one). When ``None`` and the
                PID is new, ``time.monotonic()`` is used.

        Returns:
            The registered ``SubagentIdentity`` (new or pre-existing).
        """
        if source not in _SUBAGENT_SOURCES:
            msg = (
                f"unknown subagent source {source!r}; expected one of"
                f" {sorted(_SUBAGENT_SOURCES)}"
            )
            raise ValueError(msg)
        with self._lock:
            existing = self._identities.get(pid)
            if existing is not None:
                return existing
            if now is None:
                now = time.monotonic()
            identity = SubagentIdentity(
                pid=pid,
                source=source,
                registered_at_monotonic=now,
                label_prefix=label_prefix,
            )
            # bounded-accumulator-ok: FIFO cap at 1024 entries (collections.OrderedDict
            # ``popitem(last=False)`` eviction; cap is module-level constant).
            self._identities[pid] = identity
            while len(self._identities) > _MAX_REGISTRY_ENTRIES:
                self._identities.popitem(last=False)
            self._known_pids_cache = None
            return identity

    def unregister(self, pid: int) -> None:
        """Remove a PID from the registry. No-op when the PID is unknown."""
        with self._lock:
            if pid in self._identities:
                del self._identities[pid]
                self._known_pids_cache = None

    def known_pids(self) -> frozenset[int]:
        """Return a stable frozenset of registered PIDs.

        The result is a snapshot; mutations after the call do NOT affect
        the returned frozenset. The cache is invalidated on every mutation
        so a subsequent call re-reads the registry.
        """
        with self._lock:
            cached = self._known_pids_cache
            if cached is not None:
                return cached
            pids = frozenset(self._identities.keys())
            self._known_pids_cache = pids
            return pids

    def snapshot(self) -> tuple[SubagentIdentity, ...]:
        """Return a stable tuple snapshot of all registered identities.

        Ordered by registration time (FIFO: oldest first). The returned
        tuple is a fresh copy -- callers may sort or index without
        affecting the registry's internal state.
        """
        with self._lock:
            return tuple(self._identities.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._identities)

    def __contains__(self, pid: object) -> bool:
        if not isinstance(pid, int):
            return False
        with self._lock:
            return pid in self._identities


__all__ = [
    "SubagentIdentity",
    "SubagentPidRegistry",
]
