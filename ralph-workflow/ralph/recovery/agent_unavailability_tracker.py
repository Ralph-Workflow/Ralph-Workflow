
from __future__ import annotations

import ast
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from ralph.agents.timeout_clock import SystemClock
from ralph.recovery.unavailability_reason import (
    _UNIVERSAL_COOLDOWN_CAP_MS,
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    ReasonBackoffPolicy,
    UnavailabilityReason,
)

if TYPE_CHECKING:
    from ralph.agents.timeout_clock import Clock


@runtime_checkable
class UnavailabilityStore(Protocol):

    @property
    def scope(self) -> Literal["session", "persistent"]:
        ...

    def mark_unavailable(
        self,
        phase: str,
        agent: str,
        reason: UnavailabilityReason | None = None,
    ) -> UnavailabilityEntry:
        ...

    def is_available(self, phase: str, agent: str) -> bool:
        ...

    def earliest_unavailable_wait_ms(self, phase: str, agents: list[str]) -> int:
        ...

    def reset_backoff(self, phase: str, agent: str) -> None:
        ...

    def snapshot(self) -> dict[str, dict[str, object]]:
        ...


@dataclass(frozen=True)
class UnavailabilityEntry:

    unavailable_until_ms: int
    reason: UnavailabilityReason | None
    attempt: int
    base_backoff_ms: int
    max_backoff_ms: int


DEFAULT_LEGACY_BACKOFF_MS = 5_000
DEFAULT_LEGACY_MAX_BACKOFF_MS = 300_000


class AgentUnavailabilityTracker:

    def __init__(
        self,
        clock: Clock | None = None,
        backoff_policy: dict[UnavailabilityReason, ReasonBackoffPolicy] | None = None,
        initial_entries: dict[str, UnavailabilityEntry] | None = None,
        initial_timeouts: dict[str, int] | None = None,
        scope: Literal["session", "persistent"] = "session",
        initial_backoff_attempts: dict[str, int] | None = None,
    ) -> None:
        self._scope = scope
        self._lock = threading.RLock()
        self._clock = clock or SystemClock()
        self._backoff_policy: dict[UnavailabilityReason, ReasonBackoffPolicy] = (
            backoff_policy if backoff_policy is not None else DEFAULT_UNAVAILABILITY_BACKOFF_POLICY
        )
        self._entries: dict[str, UnavailabilityEntry] = dict(initial_entries or {})
        self._backoff_attempts: dict[str, int] = dict(
            initial_backoff_attempts or {}
        )

        if initial_timeouts:
            for key, timeout_ms in initial_timeouts.items():
                self._entries[key] = UnavailabilityEntry(
                    unavailable_until_ms=timeout_ms,
                    reason=None,
                    attempt=self._backoff_attempts.get(key, 0),
                    base_backoff_ms=DEFAULT_LEGACY_BACKOFF_MS,
                    max_backoff_ms=DEFAULT_LEGACY_MAX_BACKOFF_MS,
                )

    def mark_unavailable(
        self,
        phase: str,
        agent: str,
        reason: UnavailabilityReason | None = None,
    ) -> UnavailabilityEntry:
        with self._lock:
            del phase
            key = agent
            current_time_ms = int(self._clock.monotonic() * 1000)
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
            cap_ms_int: int = min(int(cap_ms), _UNIVERSAL_COOLDOWN_CAP_MS)

            saturated_attempt = ((cap_ms_int - 1) // base_ms_int).bit_length()
            attempt = min(attempt, saturated_attempt)
            backoff_ms = min(base_ms_int << attempt, cap_ms_int)

            unavailable_until_ms = current_time_ms + backoff_ms
            self._entries[key] = UnavailabilityEntry(
                unavailable_until_ms=unavailable_until_ms,
                reason=reason,
                attempt=attempt,
                base_backoff_ms=base_ms_int,
                max_backoff_ms=cap_ms_int,
            )
            self._backoff_attempts[key] = min(attempt + 1, saturated_attempt)
            return self._entries[key]

    def is_available(self, phase: str, agent: str) -> bool:
        with self._lock:
            del phase
            entry = self._entries.get(agent)
            if entry is None:
                return True
            current_time_ms = int(self._clock.monotonic() * 1000)
            return current_time_ms >= entry.unavailable_until_ms

    def earliest_unavailable_wait_ms(self, phase: str, agents: list[str]) -> int:
        with self._lock:
            del phase
            current_time_ms = int(self._clock.monotonic() * 1000)
            min_remaining: int | None = None
            for agent in agents:
                entry = self._entries.get(agent)
                if entry is None:
                    return 0
                if entry.unavailable_until_ms > current_time_ms:
                    remaining = entry.unavailable_until_ms - current_time_ms
                    if min_remaining is None or remaining < min_remaining:
                        min_remaining = remaining
            return max(0, min_remaining or 0)

    def reset_backoff(self, phase: str, agent: str) -> None:
        with self._lock:
            del phase
            self._entries.pop(agent, None)
            self._backoff_attempts.pop(agent, None)

    def prune_expired(self, now_ms: int | None = None) -> int:
        with self._lock:
            if now_ms is None:
                now_ms = int(self._clock.monotonic() * 1000)
            expired_keys = [
                key for key, entry in self._entries.items() if now_ms >= entry.unavailable_until_ms
            ]
            for key in expired_keys:
                self._entries.pop(key, None)
            return len(expired_keys)

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                "unavailable_timeouts": {
                    key: entry.unavailable_until_ms for key, entry in self._entries.items()
                },
                "unavailability_reasons": {
                    key: str(entry.reason) if entry.reason is not None else None
                    for key, entry in self._entries.items()
                },
                "backoff_attempts": dict(self._backoff_attempts),
            }

    @property
    def scope(self) -> Literal["session", "persistent"]:
        return self._scope


__all__ = ["AgentUnavailabilityTracker", "UnavailabilityEntry", "UnavailabilityStore"]



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
    for d in member.decorator_list:
        if isinstance(d, ast.Name) and d.id == "property":
            return True
        if isinstance(d, ast.Attribute) and d.attr == "property":
            return True
    return False


def _classify_async_function(member: ast.AsyncFunctionDef) -> str:
    if member.name.startswith("_"):
        return "skip"
    return "unknown"


def _classify_sync_function(member: ast.FunctionDef) -> str | None:
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
    if isinstance(member, ast.AsyncFunctionDef):
        return _classify_async_function(member)
    if isinstance(member, ast.FunctionDef):
        return _classify_sync_function(member)
    return None


def _find_tracker_class_node(tree: ast.Module) -> ast.ClassDef:
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
    unknown: list[str] = []
    for member in class_node.body:
        kind = _classify_member(member)
        if kind == "unknown" and isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            unknown.append(member.name)
    return unknown


def _assert_no_permanent_skip_invariant() -> None:
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
