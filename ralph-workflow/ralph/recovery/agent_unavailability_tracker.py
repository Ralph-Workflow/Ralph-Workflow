"""Agent unavailability tracker with per-reason exponential backoff.

Sole owner of unavailable storage. RecoveryController delegates to this class
instead of directly managing _unavailable_timeouts and _backoff_attempts dicts.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from ralph.agents.timeout_clock import SystemClock
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    ReasonBackoffPolicy,
    UnavailabilityReason,
)

if TYPE_CHECKING:
    from ralph.agents.timeout_clock import Clock


@runtime_checkable
class UnavailabilityStore(Protocol):
    """Protocol defining the interface for an agent unavailability store.

    This store tracks which agents are currently unavailable due to errors
    (such as out of credits or suspicious timeouts) and computes backoff cooldowns.

    Callers MUST NOT depend on the dict-shaped snapshot() output for any
    cross-session use, as the snapshot format is legacy and the store may be swapped
    for a persistent implementation (sqlite, redis, file) in the future.
    """

    @property
    def scope(self) -> Literal["session", "persistent"]:
        """The scope of the store (e.g. 'session' or 'persistent')."""
        ...

    def mark_unavailable(
        self,
        phase: str,
        agent: str,
        reason: UnavailabilityReason | None = None,
    ) -> UnavailabilityEntry:
        """Mark an agent unavailable with per-reason exponential backoff."""
        ...

    def is_available(self, phase: str, agent: str) -> bool:
        """Return True when the agent is not currently marked unavailable."""
        ...

    def earliest_unavailable_wait_ms(self, phase: str, agents: list[str]) -> int:
        """Return milliseconds until the earliest unavailable agent becomes available."""
        ...

    def reset_backoff(self, phase: str, agent: str) -> None:
        """Clear the unavailable entry for a phase:agent."""
        ...

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Return a defensive copy of the internal state."""
        ...


@dataclass(frozen=True)
class UnavailabilityEntry:
    """An agent's unavailable entry with backoff state."""

    unavailable_until_ms: int
    reason: UnavailabilityReason | None
    attempt: int
    base_backoff_ms: int
    max_backoff_ms: int


DEFAULT_LEGACY_BACKOFF_MS = 5_000
DEFAULT_LEGACY_MAX_BACKOFF_MS = 300_000


class AgentUnavailabilityTracker:
    """Tracks agent unavailability with per-reason exponential backoff.

    Sole owner of unavailable storage. The RecoveryController delegates to
    this class rather than managing _unavailable_timeouts and _backoff_attempts
    directly.

    Args:
        clock: Clock for time-dependent decisions. Defaults to system clock.
        backoff_policy: Per-reason backoff policy mapping. Defaults to
            DEFAULT_UNAVAILABILITY_BACKOFF_POLICY.
        initial_entries: Optional pre-seeded entries (for testing).
        initial_timeouts: Legacy seam — optional pre-seeded timeouts dict
            (for backward compatibility with tests that use the old
            unavailable_timeouts dict).
        scope: Literal 'session' or 'persistent' indicating the storage scope.
    """

    def __init__(
        self,
        clock: Clock | None = None,
        backoff_policy: dict[UnavailabilityReason, ReasonBackoffPolicy] | None = None,
        initial_entries: dict[str, UnavailabilityEntry] | None = None,
        initial_timeouts: dict[str, int] | None = None,
        scope: Literal["session", "persistent"] = "session",
    ) -> None:
        self._scope = scope
        self._clock = clock or SystemClock()
        self._backoff_policy: dict[UnavailabilityReason, ReasonBackoffPolicy] = (
            backoff_policy if backoff_policy is not None else DEFAULT_UNAVAILABILITY_BACKOFF_POLICY
        )
        self._entries: dict[str, UnavailabilityEntry] = dict(initial_entries or {})
        self._backoff_attempts: dict[str, int] = {}

        if initial_timeouts:
            for key, timeout_ms in initial_timeouts.items():
                self._entries[key] = UnavailabilityEntry(
                    unavailable_until_ms=timeout_ms,
                    reason=None,
                    attempt=0,
                    base_backoff_ms=DEFAULT_LEGACY_BACKOFF_MS,
                    max_backoff_ms=DEFAULT_LEGACY_MAX_BACKOFF_MS,
                )

    def mark_unavailable(
        self,
        phase: str,
        agent: str,
        reason: UnavailabilityReason | None = None,
    ) -> UnavailabilityEntry:
        """Mark an agent unavailable with per-reason exponential backoff.

        Args:
            phase: Pipeline phase.
            agent: Agent name.
            reason: The unavailability reason (determines backoff policy).

        Returns:
            The new UnavailabilityEntry with computed backoff.
        """
        key = f"{phase}:{agent}"
        current_time_ms = int(self._clock.monotonic() * 1000)
        # Opportunistically prune expired entries so the dict does
        # not grow without bound across long parallel runs.
        self.prune_expired(now_ms=current_time_ms)
        attempt: int = self._backoff_attempts.get(key, 0)

        if reason is not None and reason in self._backoff_policy:
            policy = self._backoff_policy[reason]
        else:
            policy = None

        if policy is not None:
            base_ms = policy.base_backoff_ms
            cap_ms = policy.max_backoff_ms
        else:
            base_ms = DEFAULT_LEGACY_BACKOFF_MS
            cap_ms = DEFAULT_LEGACY_MAX_BACKOFF_MS

        base_ms_int: int = int(base_ms)
        cap_ms_int: int = int(cap_ms)

        multiplier: int = pow(2, attempt)
        backoff_ms: int = base_ms_int * multiplier
        backoff_ms = min(backoff_ms, cap_ms_int)

        unavailable_until_ms = current_time_ms + backoff_ms
        self._entries[key] = UnavailabilityEntry(
            unavailable_until_ms=unavailable_until_ms,
            reason=reason,
            attempt=attempt,
            base_backoff_ms=base_ms_int,
            max_backoff_ms=cap_ms_int,
        )
        self._backoff_attempts[key] = attempt + 1
        return self._entries[key]

    def is_available(self, phase: str, agent: str) -> bool:
        """Return True when the agent is not currently marked unavailable."""
        key = f"{phase}:{agent}"
        entry = self._entries.get(key)
        if entry is None:
            return True
        current_time_ms = int(self._clock.monotonic() * 1000)
        return current_time_ms >= entry.unavailable_until_ms

    def earliest_unavailable_wait_ms(self, phase: str, agents: list[str]) -> int:
        """Return milliseconds until the earliest unavailable agent becomes available.

        Returns 0 if any agent is available.
        """
        current_time_ms = int(self._clock.monotonic() * 1000)
        min_remaining: int | None = None
        for agent in agents:
            key = f"{phase}:{agent}"
            entry = self._entries.get(key)
            if entry is None:
                return 0
            if entry.unavailable_until_ms > current_time_ms:
                remaining = entry.unavailable_until_ms - current_time_ms
                if min_remaining is None or remaining < min_remaining:
                    min_remaining = remaining
        return max(0, min_remaining or 0)

    def reset_backoff(self, phase: str, agent: str) -> None:
        """Clear the unavailable entry for a phase:agent."""
        key = f"{phase}:{agent}"
        self._entries.pop(key, None)
        self._backoff_attempts.pop(key, None)

    def prune_expired(self, now_ms: int | None = None) -> int:
        """Remove entries whose cooldown has elapsed at ``now_ms``.

        Mirrors the opportunistic-sweep pattern in
        ``ChildLivenessRegistry.prune_stale``: the tracker is
        unbounded on the ``_entries`` axis (only ``reset_backoff``
        ever pops a single key), so a long-lived pipeline that
        accumulates hundreds of (phase, agent) pairs would retain
        every expired entry forever. ``prune_expired`` drops the
        expired entries WITHOUT touching ``_backoff_attempts`` so
        exponential backoff continues across fail/recover cycles
        even after pruning.

        Args:
            now_ms: Current monotonic time in milliseconds. When
                ``None`` (default), uses the injected clock.

        Returns:
            Number of entries pruned.
        """
        if now_ms is None:
            now_ms = int(self._clock.monotonic() * 1000)
        expired_keys = [
            key
            for key, entry in self._entries.items()
            if now_ms >= entry.unavailable_until_ms
        ]
        for key in expired_keys:
            self._entries.pop(key, None)
        return len(expired_keys)

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Return a defensive copy of the internal state."""
        return {
            "unavailable_timeouts": {
                key: entry.unavailable_until_ms for key, entry in self._entries.items()
            },
            "backoff_attempts": dict(self._backoff_attempts),
        }

    @property
    def scope(self) -> Literal["session", "persistent"]:
        """The scope of the store (e.g. 'session' or 'persistent')."""
        return self._scope


__all__ = ["AgentUnavailabilityTracker", "UnavailabilityEntry", "UnavailabilityStore"]


# ---------------------------------------------------------------------------
# No permanent skip rule (locked at import time)
# ---------------------------------------------------------------------------
#
# An agent is NEVER marked permanently unavailable. The only two public
# mutators on the unavailable set are ``mark_unavailable`` (adds with
# exponential backoff) and ``reset_backoff`` (removes on cooldown expiry
# or explicit reset). The remaining public surface
# (``is_available``, ``earliest_unavailable_wait_ms``, ``snapshot``,
# ``scope``) is read-only. The constructor (``__init__``) is allowed to
# seed ``_entries`` from ``initial_timeouts`` because that is operator-
# provided config, not a runtime mutation.
#
# The pipeline never assumes an agent is permanently broken; any agent
# may become available again for any reason (e.g. user upgrades their
# plan, infrastructure recovers). The check uses ``if/raise RuntimeError``
# (NOT ``assert``) so it survives ``python -O`` per AGENTS.md.

_ALLOWED_PUBLIC_MUTATORS: frozenset[str] = frozenset(
    {"mark_unavailable", "reset_backoff", "prune_expired"}
)
_READONLY_PUBLIC_METHODS: frozenset[str] = frozenset(
    {
        "is_available",
        "earliest_unavailable_wait_ms",
        "snapshot",
    }
)
_READONLY_PROPERTIES: frozenset[str] = frozenset({"scope"})


def _is_property_decorated(member: ast.FunctionDef) -> bool:
    """Return True when a class member is decorated with ``@property``."""
    for d in member.decorator_list:
        if isinstance(d, ast.Name) and d.id == "property":
            return True
        if isinstance(d, ast.Attribute) and d.attr == "property":
            return True
    return False


def _classify_async_function(member: ast.AsyncFunctionDef) -> str:
    """Classify an async function member (``skip`` or ``unknown``)."""
    if member.name.startswith("_"):
        return "skip"
    return "unknown"


def _classify_sync_function(member: ast.FunctionDef) -> str | None:
    """Classify a sync function member.

    Returns ``None`` for class-level non-method members and one of
    ``"skip"``, ``"mutator"``, ``"readonly"``, ``"unknown"`` for
    methods. Properties decorated with ``@property`` follow the
    read-only allowlist; the other public methods follow the
    mutator/read-only allowlist.
    """
    if member.name.startswith("_"):
        return "skip"
    if _is_property_decorated(member):
        return "readonly" if member.name in _READONLY_PROPERTIES else "unknown"
    if member.name in _ALLOWED_PUBLIC_MUTATORS:
        return "mutator"
    if member.name in _READONLY_PUBLIC_METHODS:
        return "readonly"
    return "unknown"


def _classify_member(member: ast.AST) -> str | None:
    """Classify a class member for the no-permanent-skip invariant check.

    Returns:
        "skip" -- the member is private (underscore-prefixed) and ignored.
        "mutator" -- a public mutator in the allowlist.
        "readonly" -- a public read-only method or property in the allowlist.
        "unknown" -- a public method/property NOT in any allowlist; a
            contract violation that fails the invariant.
        None -- the member is not a method (e.g. a class-level constant).
    """
    if isinstance(member, ast.AsyncFunctionDef):
        return _classify_async_function(member)
    if isinstance(member, ast.FunctionDef):
        return _classify_sync_function(member)
    return None


def _find_tracker_class_node(tree: ast.Module) -> ast.ClassDef:
    """Find the ``AgentUnavailabilityTracker`` class in the module AST.

    Raises ``RuntimeError`` with the invariant-violation message when
    the class is not found. The error path is split from the main
    invariant function so the class lookup is easy to follow in
    isolation.
    """
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AgentUnavailabilityTracker":
            return node
    msg = (
        "No-permanent-skip invariant violated: AgentUnavailabilityTracker"
        " class not found in ralph.recovery.agent_unavailability_tracker."
        " Restore the class definition or update the import-time"
        " invariant in ralph.recovery.agent_unavailability_tracker."
    )
    raise RuntimeError(msg)


def _parse_tracker_source(source_path: Path) -> ast.Module:
    """Parse the tracker source file or raise a RuntimeError.

    Splits the parse step from the invariant function so the
    ``SyntaxError``-to-``RuntimeError`` translation is one short
    function call.
    """
    source = source_path.read_text(encoding="utf-8")
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        msg = (
            "agent_unavailability_tracker.py failed to parse during the"
            " no-permanent-skip invariant check. The tracker source is"
            " broken; the no-permanent-skip rule cannot be verified."
            f" Source: {source_path}. Parser error: {exc}"
        )
        raise RuntimeError(msg) from exc


def _collect_unknown_public_methods(class_node: ast.ClassDef) -> list[str]:
    """Return the names of public methods/properties that are not allowlisted."""
    unknown: list[str] = []
    for member in class_node.body:
        kind = _classify_member(member)
        if kind == "unknown" and isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            unknown.append(member.name)
    return unknown


def _assert_no_permanent_skip_invariant() -> None:
    """Verify the no-permanent-skip rule on ``AgentUnavailabilityTracker``.

    The check reads the tracker's source file from disk (not a compiled
    bytecode cache) so it always reflects the current source. The only
    public methods that may mutate the unavailable set are the two
    allowed mutators; every other public method on the class must be
    read-only. Adding a new mutator is a deliberate contract change
    and must be paired with an update to the import-time invariant
    and the test in ``tests/recovery/test_two_state_invariant.py``.
    """
    source_path = Path(__file__).resolve()
    tree = _parse_tracker_source(source_path)
    class_node = _find_tracker_class_node(tree)
    unknown = _collect_unknown_public_methods(class_node)
    if unknown:
        methods = ", ".join(sorted(unknown))
        msg = (
            "No-permanent-skip invariant violated: AgentUnavailabilityTracker"
            f" has public method(s) that are not in the allowlist: {methods}."
            " The only public mutators on the unavailable set are"
            " mark_unavailable and reset_backoff. Every other public method"
            " must be read-only (is_available, earliest_unavailable_wait_ms,"
            " snapshot) or a read-only property (scope). Adding a new mutator"
            " is a deliberate contract change; update the import-time"
            " invariant and the test in"
            " tests/recovery/test_two_state_invariant.py in the same commit."
        )
        raise RuntimeError(msg)


_assert_no_permanent_skip_invariant()
