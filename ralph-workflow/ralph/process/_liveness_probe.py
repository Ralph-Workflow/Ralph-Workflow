from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildActivitySnapshot


@runtime_checkable
class LivenessProbe(Protocol):
    def any_agent_active(self, label_prefix: str) -> bool: ...

    def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot: ...
