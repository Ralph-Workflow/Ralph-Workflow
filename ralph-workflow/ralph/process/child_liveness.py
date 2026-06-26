"""In-memory child liveness lease registry and canonical evidence classifier.

This module is the single source of truth for child-evidence freshness decisions.
``classify_child_snapshot()`` is the canonical verdict function: both the
in-stream idle-watchdog path (``execution_state.classify_quiet``) and the post-exit
path (``execution_state.classify_exit`` / ``invoke._evidence_precedence``) must call
this function rather than re-encoding the stale-vs-fresh precedence rules independently.

Evidence is tracked per-child with heartbeat, progress, and terminal-ack signals
using an injectable clock so tests can use deterministic FakeClock-compatible sources.

No on-disk persistence: the registry is instantiated per invoke and lives only as
long as the invocation.
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING

from ralph.process._alive_by import AliveBy
from ralph.process._child_activity_snapshot import ChildActivitySnapshot
from ralph.process._child_evidence_verdict import ChildEvidenceVerdict
from ralph.process._child_liveness_record import ChildLivenessRecord
from ralph.process._mutable_record import MutableRecord

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "AliveBy",
    "ChildActivitySnapshot",
    "ChildEvidenceVerdict",
    "ChildLivenessRecord",
    "ChildLivenessRegistry",
    "classify_child_snapshot",
]


class ChildLivenessSubagentPidSource:
    """Subagent PID source backed by a ChildLivenessRegistry.

    OpenCode emits structured child lifecycle events (``child_started``,
    ``child_progress``, ``child_heartbeat``, ``child_complete``) on its
    stdout stream. Those events carry the child PID and are ingested by
    ``OpenCodeExecutionStrategy`` into a per-invocation
    ``ChildLivenessRegistry``. Because the registry is first-party
    evidence produced by the agent's own output, using it to identify
    spawned subagent PIDs is documentation-grounded for OpenCode and
    avoids broad command-line heuristics.

    The source is injected into ``DefaultProcessMonitor`` so the monitor
    can promote known subagent PIDs to ``ProcessRole.SPAWNED_SUBAGENT``
    while remaining transport-agnostic in structure.

    Args:
        registry: The ``ChildLivenessRegistry`` that owns scoped child records.
        scope_prefix: Scope prefix used to narrow records to the current
            invocation. For OpenCode this is typically ``agent:{label_scope}:``.
    """

    def __init__(
        self,
        registry: ChildLivenessRegistry,
        scope_prefix: str = "",
    ) -> None:
        self._registry = registry
        self._scope_prefix = scope_prefix

    def known_subagent_pids(self) -> set[int]:
        """Return active subagent PIDs from the registry."""
        return self._registry.active_pids(self._scope_prefix)


def classify_child_snapshot(
    snapshot: ChildActivitySnapshot,
    *,
    has_os_descendants: bool = False,
) -> ChildEvidenceVerdict:
    """Classify child-liveness evidence from a snapshot into a typed verdict.

    This is the single source of truth for stale/fresh precedence logic.
    Both execution_state and invoke corroboration must consume this function
    rather than re-implementing the precedence rules independently.

    Args:
        snapshot: Freshness-aware aggregate snapshot from a registry or probe.
        has_os_descendants: Whether OS-level descendants exist (from process tree scan).
            Only consulted when no scoped Ralph evidence is present.

    Returns:
        A ChildEvidenceVerdict encoding alive_by classification, deferral_allowed,
        and all_children_terminal flags.
    """
    # All Ralph-tracked children are terminal and none remain active.
    if snapshot.terminal_count > 0 and snapshot.active_count == 0:
        return ChildEvidenceVerdict(
            alive_by=None, deferral_allowed=False, all_children_terminal=True
        )

    if snapshot.has_fresh_progress:
        return ChildEvidenceVerdict(alive_by=AliveBy.FRESH_PROGRESS, deferral_allowed=True)

    if snapshot.has_fresh_heartbeat and snapshot.has_process:
        return ChildEvidenceVerdict(alive_by=AliveBy.FRESH_HEARTBEAT_ONLY, deferral_allowed=True)

    if snapshot.has_process:
        # Child is in registry but has no fresh heartbeat or progress.
        # Deferral is allowed only while the label is still fresh (stale_label_ttl
        # grace window) to preserve the grace period for newly registered children
        # that have not yet sent their first heartbeat.
        return ChildEvidenceVerdict(
            alive_by=AliveBy.STALE_LABEL_ONLY,
            deferral_allowed=snapshot.has_fresh_label,
        )

    # No scoped Ralph evidence at all — fall back to OS descendants if present.
    if has_os_descendants:
        # Property G: do NOT allow deferral just because the agent has
        # descendant processes. The watchdog exists to recognize that a
        # session is wedged even when the agent spawned unrelated, long-lived
        # children (ng mcp, playwright, etc.). Deferral permission must come
        # from Ralph's own progress signals, not from process tree presence.
        return ChildEvidenceVerdict(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            deferral_allowed=False,
        )

    return ChildEvidenceVerdict(alive_by=None, deferral_allowed=False)


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
        self._records: dict[str, MutableRecord] = {}  # bounded-accumulator-ok: drained on reap

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
        self._records[child_id] = MutableRecord(
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

    def has_records(self, scope_prefix: str) -> bool:
        """Return True when any record currently matches the given scope prefix."""
        return any(rec.scope_prefix.startswith(scope_prefix) for rec in self._records.values())

    def snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
        """Return an aggregated freshness snapshot for all children matching scope_prefix."""
        now = self._now()
        self.prune_stale(now)
        active_count = 0
        terminal_count = 0
        has_process = False
        has_fresh_label = False
        has_fresh_heartbeat = False
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
            # has_fresh_label: child was registered within stale_label_ttl,
            # OR had a heartbeat within heartbeat_ttl (heartbeat freshness
            # overrides stale label to suppress false-positive timeouts during
            # transient network/lifecycle events).
            label_age = now - rec.started_at
            child_has_fresh_label = label_age <= self._stale_label_ttl
            if not child_has_fresh_label and rec.last_heartbeat_at is not None:
                heartbeat_age = now - rec.last_heartbeat_at
                if heartbeat_age <= self._heartbeat_ttl:
                    child_has_fresh_label = True
            has_fresh_label = has_fresh_label or child_has_fresh_label

            # has_fresh_heartbeat: child had a heartbeat within heartbeat_ttl but NOT
            # counted as progress. Distinct from has_fresh_label because has_fresh_label
            # can also be True due to recent registration (within stale_label_ttl).
            if rec.last_heartbeat_at is not None:
                heartbeat_age = now - rec.last_heartbeat_at
                if heartbeat_age <= self._heartbeat_ttl:
                    has_fresh_heartbeat = True

            # has_fresh_progress: child produced a progress signal within progress_ttl
            if rec.last_progress_at is not None:
                progress_age = now - rec.last_progress_at
                if progress_age <= self._progress_ttl:
                    has_fresh_progress = True

        return ChildActivitySnapshot(
            scope_prefix=scope_prefix,
            has_process=has_process,
            has_fresh_label=has_fresh_label,
            has_fresh_heartbeat=has_fresh_heartbeat,
            has_fresh_progress=has_fresh_progress,
            oldest_live_child_seconds=oldest_live_child_seconds,
            active_count=active_count,
            terminal_count=terminal_count,
        )

    def active_pids(self, scope_prefix: str) -> set[int]:
        """Return non-terminal PIDs of children matching ``scope_prefix``.

        Prunes stale records first so returned PIDs correspond to children
        whose evidence has not yet aged out of the registry.
        """
        t = self._now()
        self.prune_stale(t)
        pids: set[int] = set()
        for rec in self._records.values():
            if not rec.scope_prefix.startswith(scope_prefix):
                continue
            if rec.terminal_state is not None:
                continue
            if rec.pid is not None:
                pids.add(rec.pid)
        return pids

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
            heartbeat_fresh = False
            if rec.last_heartbeat_at is not None:
                heartbeat_fresh = (t - rec.last_heartbeat_at) <= self._heartbeat_ttl
            if rec.last_progress_at is not None:
                progress_stale = (t - rec.last_progress_at) > self._progress_ttl
                if progress_stale and not heartbeat_fresh:
                    to_prune.append(child_id)
            else:
                label_age = t - rec.started_at
                if label_age > self._stale_label_ttl and not heartbeat_fresh:
                    to_prune.append(child_id)
        for child_id in to_prune:
            del self._records[child_id]
        return len(to_prune)
