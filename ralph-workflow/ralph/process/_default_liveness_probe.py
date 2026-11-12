from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.process.child_liveness import ChildActivitySnapshot
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildLivenessRegistry


class DefaultLivenessProbe:
    def __init__(self, registry: ChildLivenessRegistry | None = None) -> None:
        self._registry = registry

    def any_agent_active(self, label_prefix: str) -> bool:
        return any(
            r.label is not None and r.label.startswith(label_prefix)
            for r in get_process_manager().list_active()
        )

    def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
        has_process = False
        active_count = 0
        if scope_prefix:
            active_records = get_process_manager().list_active()
            for r in active_records:
                if r.label is not None and r.label.startswith(scope_prefix):
                    has_process = True
                    active_count += 1

        if self._registry is not None:
            reg_snap = self._registry.snapshot(scope_prefix)
            return ChildActivitySnapshot(
                scope_prefix=scope_prefix,
                has_process=has_process or reg_snap.has_process,
                has_fresh_label=reg_snap.has_fresh_label,
                has_fresh_progress=reg_snap.has_fresh_progress,
                oldest_live_child_seconds=reg_snap.oldest_live_child_seconds,
                active_count=max(active_count, reg_snap.active_count),
                terminal_count=reg_snap.terminal_count,
            )

        return ChildActivitySnapshot(
            scope_prefix=scope_prefix,
            has_process=has_process,
            has_fresh_label=has_process,
            has_fresh_progress=False,
            oldest_live_child_seconds=None,
            active_count=active_count,
            terminal_count=0,
        )
