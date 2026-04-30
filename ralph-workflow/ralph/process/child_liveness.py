"""In-memory child liveness lease registry for OpenCode child process tracking.

Tracks per-child evidence (heartbeat, progress, terminal ack) with an injectable
clock so tests can use deterministic FakeClock-compatible now sources.

No on-disk persistence in v1: the registry is instantiated per invoke and lives
only as long as the invocation.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "ChildActivitySnapshot",
    "ChildLivenessRecord",
    "ChildLivenessRegistry",
]


@dataclass(frozen=True)
class ChildLivenessRecord:
    """Immutable snapshot of a single child's liveness state."""

    child_id: str
    scope_prefix: str
    pid: int | None
    started_at: float
    last_progress_at: float | None
    last_heartbeat_at: float | None
    last_ack_at: float | None
    last_known_phase: str = "spawned"
    terminal_state: str | None = None
    lease_expires_at: float | None = None


@dataclass(frozen=True)
class ChildActivitySnapshot:
    """Freshness-aware aggregate snapshot for a scope prefix."""

    scope_prefix: str
    has_process: bool
    has_fresh_label: bool
    has_fresh_progress: bool
    oldest_live_child_seconds: float | None
    active_count: int
    terminal_count: int


@dataclass
class _MutableRecord:
    child_id: str
    scope_prefix: str
    pid: int | None
    started_at: float
    last_progress_at: float | None = None
    last_heartbeat_at: float | None = None
    last_ack_at: float | None = None
    last_known_phase: str = "spawned"
    terminal_state: str | None = None
    lease_expires_at: float | None = None


class ChildLivenessRegistry:
    """In-memory registry of active child leases with freshness tracking.

    All methods are synchronous and safe to call from the main thread. The registry
    is not thread-safe by design: the invoke loop drives all operations from a
    single call site.

    Args:
        progress_ttl: Seconds since last progress signal before child is stale.
        heartbeat_ttl: Seconds since last heartbeat before heartbeat is stale.
        stale_label_ttl: Grace period (seconds) after evidence goes stale.
        exit_reconcile: Window (seconds) after terminal ack during which the
            record is retained before being dropped from active counts.
        now: Callable returning current monotonic time; defaults to time.monotonic.
    """

    def __init__(
        self,
        *,
        progress_ttl: float,
        heartbeat_ttl: float,
        stale_label_ttl: float,
        exit_reconcile: float,
        now: Callable[[], float] = _time.monotonic,
    ) -> None:
        self._progress_ttl = progress_ttl
        self._heartbeat_ttl = heartbeat_ttl
        self._stale_label_ttl = stale_label_ttl
        self._exit_reconcile = exit_reconcile
        self._now = now
        self._records: dict[str, _MutableRecord] = {}

    def register_child(
        self,
        child_id: str,
        scope_prefix: str,
        *,
        pid: int | None = None,
        phase: str = "spawned",
    ) -> None:
        """Register a new child with the registry."""
        t = self._now()
        self._records[child_id] = _MutableRecord(
            child_id=child_id,
            scope_prefix=scope_prefix,
            pid=pid,
            started_at=t,
            last_known_phase=phase,
        )

    def record_heartbeat(self, child_id: str) -> None:
        """Record a heartbeat for a child (advances last_heartbeat_at only)."""
        rec = self._records.get(child_id)
        if rec is None:
            return
        rec.last_heartbeat_at = self._now()

    def record_progress(self, child_id: str, *, phase: str | None = None) -> None:
        """Record progress for a child (advances both progress and heartbeat)."""
        rec = self._records.get(child_id)
        if rec is None:
            return
        t = self._now()
        rec.last_progress_at = t
        rec.last_heartbeat_at = t
        if phase is not None:
            rec.last_known_phase = phase

    def record_terminal_ack(self, child_id: str, *, terminal_state: str = "complete") -> None:
        """Record that a child has terminated."""
        rec = self._records.get(child_id)
        if rec is None:
            return
        t = self._now()
        rec.last_ack_at = t
        rec.terminal_state = terminal_state

    def snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
        """Return an aggregated freshness snapshot for all children matching scope_prefix."""
        now = self._now()
        active_count = 0
        terminal_count = 0
        has_process = False
        has_fresh_label = False
        has_fresh_progress = False
        oldest_live_child_seconds: float | None = None

        for rec in self._records.values():
            if not rec.scope_prefix.startswith(scope_prefix):
                continue

            if rec.terminal_state is not None:
                # Terminal record: count it but exclude from active evidence
                # unless still inside reconcile window
                terminal_count += 1
                if rec.last_ack_at is not None and (now - rec.last_ack_at) <= self._exit_reconcile:
                    # Still in reconcile window: counted but not as active
                    pass
                continue

            # Active (non-terminal) child
            active_count += 1
            has_process = True

            child_age = now - rec.started_at
            if oldest_live_child_seconds is None or child_age > oldest_live_child_seconds:
                oldest_live_child_seconds = child_age

            # Freshness checks
            # has_fresh_label: child was registered within stale_label_ttl
            label_age = now - rec.started_at
            if label_age <= self._stale_label_ttl:
                has_fresh_label = True
            elif rec.last_heartbeat_at is not None:
                heartbeat_age = now - rec.last_heartbeat_at
                if heartbeat_age <= self._heartbeat_ttl:
                    has_fresh_label = True

            # has_fresh_progress: child produced a progress signal within progress_ttl
            if rec.last_progress_at is not None:
                progress_age = now - rec.last_progress_at
                if progress_age <= self._progress_ttl:
                    has_fresh_progress = True

        return ChildActivitySnapshot(
            scope_prefix=scope_prefix,
            has_process=has_process,
            has_fresh_label=has_fresh_label,
            has_fresh_progress=has_fresh_progress,
            oldest_live_child_seconds=oldest_live_child_seconds,
            active_count=active_count,
            terminal_count=terminal_count,
        )

    def prune_stale(self, now: float | None = None) -> int:
        """Remove records whose evidence is fully stale.

        A record is pruned when:
        - It has a terminal state AND the ack is outside the exit_reconcile window, OR
        - It has no terminal state AND no progress ever, AND its label age > stale_label_ttl, OR
        - It has no terminal state AND its last progress is older than progress_ttl.

        Returns:
            Number of records pruned.
        """
        t = now if now is not None else self._now()
        to_prune: list[str] = []
        for child_id, rec in self._records.items():
            if rec.terminal_state is not None:
                if rec.last_ack_at is not None and (t - rec.last_ack_at) > self._exit_reconcile:
                    to_prune.append(child_id)
                continue
            # Non-terminal: prune if progress is stale (or never happened and label is old)
            if rec.last_progress_at is not None:
                if (t - rec.last_progress_at) > self._progress_ttl:
                    to_prune.append(child_id)
            else:
                label_age = t - rec.started_at
                if label_age > self._stale_label_ttl:
                    to_prune.append(child_id)
        for child_id in to_prune:
            del self._records[child_id]
        return len(to_prune)
