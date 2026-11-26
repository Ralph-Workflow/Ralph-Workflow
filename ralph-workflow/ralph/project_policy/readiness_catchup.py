from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from loguru import logger

from ralph.pipeline.auto_integrate_catchup import start_catchup_worker_if_enabled

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.workspace.scope import WorkspaceScope


class StoppableWorker(Protocol):
    def stop(self) -> None: ...


CatchupFactory = Callable[["UnifiedConfig", Path], StoppableWorker | None]


def default_catchup_factory(config: UnifiedConfig, root: Path) -> StoppableWorker | None:
    return start_catchup_worker_if_enabled(config, root)


@contextmanager
def catchup_during_readiness(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope | None,
    factory: CatchupFactory | None,
) -> Iterator[None]:
    worker: StoppableWorker | None = None
    if workspace_scope is not None:
        try:
            worker = (factory or default_catchup_factory)(config, Path(workspace_scope.root))
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("catch-up worker could not start during policy readiness: {}", exc)
    try:
        yield
    finally:
        if worker is not None:
            worker.stop()


__all__ = [
    "CatchupFactory",
    "StoppableWorker",
    "catchup_during_readiness",
    "default_catchup_factory",
]
