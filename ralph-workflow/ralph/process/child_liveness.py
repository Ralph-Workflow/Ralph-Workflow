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
    def __init__(
        self,
        registry: ChildLivenessRegistry,
        scope_prefix: str = "",
    ) -> None:
        self._registry = registry
        self._scope_prefix = scope_prefix

    def known_subagent_pids(self) -> set[int]:
        return self._registry.active_pids(self._scope_prefix)


def classify_child_snapshot(
    snapshot: ChildActivitySnapshot,
    *,
    has_os_descendants: bool = False,
) -> ChildEvidenceVerdict:
    if snapshot.terminal_count > 0 and snapshot.active_count == 0:
        return ChildEvidenceVerdict(
            alive_by=None, deferral_allowed=False, all_children_terminal=True
        )

    if snapshot.has_fresh_progress:
        return ChildEvidenceVerdict(alive_by=AliveBy.FRESH_PROGRESS, deferral_allowed=True)

    if snapshot.has_fresh_heartbeat and snapshot.has_process:
        return ChildEvidenceVerdict(alive_by=AliveBy.FRESH_HEARTBEAT_ONLY, deferral_allowed=True)

    if snapshot.has_process:
        return ChildEvidenceVerdict(
            alive_by=AliveBy.STALE_LABEL_ONLY,
            deferral_allowed=snapshot.has_fresh_label,
        )

    if has_os_descendants:
        return ChildEvidenceVerdict(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            deferral_allowed=False,
        )

    return ChildEvidenceVerdict(alive_by=None, deferral_allowed=False)


class ChildLivenessRegistry:
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
        self._records: dict[str, MutableRecord] = {}

    def register_child(
        self,
        child_id: str,
        scope_prefix: str,
        *,
        pid: int | None = None,
        phase: str = "spawned",
    ) -> None:
        t = self._now()
        self._records[child_id] = MutableRecord(
            child_id=child_id,
            scope_prefix=scope_prefix,
            pid=pid,
            started_at=t,
            last_known_phase=phase,
        )

    def record_heartbeat(self, child_id: str) -> None:
        rec = self._records.get(child_id)
        if rec is None:
            return
        rec.last_heartbeat_at = self._now()

    def record_progress(self, child_id: str, *, phase: str | None = None) -> None:
        rec = self._records.get(child_id)
        if rec is None:
            return
        t = self._now()
        rec.last_progress_at = t
        rec.last_heartbeat_at = t
        if phase is not None:
            rec.last_known_phase = phase

    def record_terminal_ack(self, child_id: str, *, terminal_state: str = "complete") -> None:
        rec = self._records.get(child_id)
        if rec is None:
            return
        t = self._now()
        rec.last_ack_at = t
        rec.terminal_state = terminal_state

    def has_child(self, child_id: str) -> bool:
        return child_id in self._records

    def has_records(self, scope_prefix: str) -> bool:
        return any(rec.scope_prefix.startswith(scope_prefix) for rec in self._records.values())

    def snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
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
                terminal_count += 1
                if rec.last_ack_at is not None and (now - rec.last_ack_at) <= self._exit_reconcile:
                    pass
                continue

            active_count += 1
            has_process = True

            child_age = now - rec.started_at
            if oldest_live_child_seconds is None or child_age > oldest_live_child_seconds:
                oldest_live_child_seconds = child_age

            label_age = now - rec.started_at
            child_has_fresh_label = label_age <= self._stale_label_ttl
            if not child_has_fresh_label and rec.last_heartbeat_at is not None:
                heartbeat_age = now - rec.last_heartbeat_at
                if heartbeat_age <= self._heartbeat_ttl:
                    child_has_fresh_label = True
            has_fresh_label = has_fresh_label or child_has_fresh_label

            if rec.last_heartbeat_at is not None:
                heartbeat_age = now - rec.last_heartbeat_at
                if heartbeat_age <= self._heartbeat_ttl:
                    has_fresh_heartbeat = True

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

    def has_active_phase(self, scope_prefix: str, phase: str) -> bool:
        now = self._now()
        return any(
            rec.scope_prefix.startswith(scope_prefix)
            and rec.terminal_state is None
            and rec.last_known_phase == phase
            and rec.last_heartbeat_at is not None
            and (now - rec.last_heartbeat_at) <= self._heartbeat_ttl
            for rec in self._records.values()
        )

    def active_pids(self, scope_prefix: str) -> set[int]:
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
        t = now if now is not None else self._now()
        to_prune: list[str] = []
        for child_id, rec in self._records.items():
            if rec.terminal_state is not None:
                if rec.last_ack_at is not None and (t - rec.last_ack_at) > self._exit_reconcile:
                    to_prune.append(child_id)
                continue
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
