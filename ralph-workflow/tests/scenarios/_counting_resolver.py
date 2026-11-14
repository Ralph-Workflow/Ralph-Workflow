from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop, RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

__all__ = ["CountingResolver"]


class CountingResolver:
    def __init__(self, inner: RebaseStopResolver) -> None:
        self.inner = inner
        self.launches = 0
        self.stops: list[RebaseStop] = []

    def __call__(self, root: Path, target: str, stop: RebaseStop) -> bool:
        self.launches += 1
        self.stops.append(stop)
        return bool(self.inner(root, target, stop))

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession | None = None,
    ) -> bool:
        if hasattr(self.inner, "resolve_rebase"):
            return self.inner.resolve_rebase(root, target, self, session=session)
        from ralph.pipeline.integration_actions import default_agent_runner

        return default_agent_runner().resolve_rebase(root, target, self, session=session)
