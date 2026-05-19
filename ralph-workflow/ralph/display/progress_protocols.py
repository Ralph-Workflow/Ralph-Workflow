"""Protocol stubs used by :mod:`ralph.display.progress`."""

from __future__ import annotations

from typing import TYPE_CHECKING

TaskID = int

if TYPE_CHECKING:
    from typing import Protocol

    from rich.theme import Theme

    class _ConsoleProto(Protocol):
        def print(self, *_objects: object, **kwargs: object) -> None: ...

    class _ProgressProto(Protocol):
        def __enter__(self) -> _ProgressProto: ...

        def __exit__(self, exc_type: object, exc: object, _tb: object) -> bool | None: ...

        def add_task(
            self,
            description: str,
            *,
            total: int | None = ...,
            completed: int = ...,
            parent: TaskID | None = ...,
        ) -> TaskID: ...

        def update(
            self,
            *,
            task_id: TaskID,
            completed: int | None = ...,
            advance: int | None = ...,
            description: str | None = ...,
        ) -> None: ...

    class _TqdmProto(Protocol):
        n: int

        def update(self, n: int = ...) -> object: ...

        def close(self) -> None: ...

        def refresh(self) -> None: ...

    class _ConsoleFactory(Protocol):
        def __call__(self, *, stderr: bool, theme: Theme | None = ...) -> _ConsoleProto: ...

    class _ColumnFactory(Protocol):
        def __call__(self, *args: object, **kwargs: object) -> object: ...

    class _ProgressFactory(Protocol):
        def __call__(self, *columns: object, **kwargs: object) -> _ProgressProto: ...

    class _TqdmFactory(Protocol):
        def __call__(self, **kwargs: object) -> _TqdmProto: ...

    class _GetIPython(Protocol):
        def __call__(self) -> object | None: ...

    ProgressRenderer = _ProgressProto
    ActivityRenderer = _TqdmProto
    PhaseTracker = _ProgressProto

__all__ = [
    "ActivityRenderer",
    "PhaseTracker",
    "ProgressRenderer",
    "TaskID",
    "_ColumnFactory",
    "_ConsoleFactory",
    "_ConsoleProto",
    "_GetIPython",
    "_ProgressFactory",
    "_ProgressProto",
    "_TqdmFactory",
    "_TqdmProto",
]
