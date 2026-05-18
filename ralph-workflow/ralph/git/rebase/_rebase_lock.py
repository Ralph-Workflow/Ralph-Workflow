"""RebaseLock — context manager that acquires and releases the rebase lock."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable


class RebaseLock:
    """Context manager that acquires and releases the rebase lock."""

    _acquire_fn: ClassVar[Callable[[], None] | None] = None
    _release_fn: ClassVar[Callable[[], None] | None] = None

    def __init__(self) -> None:
        self.owns_lock = False

    def __enter__(self) -> RebaseLock:
        if RebaseLock._acquire_fn is None:
            raise RuntimeError("RebaseLock._acquire_fn has not been injected")
        RebaseLock._acquire_fn()
        self.owns_lock = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        if self.owns_lock:
            if RebaseLock._release_fn is None:
                raise RuntimeError("RebaseLock._release_fn has not been injected")
            RebaseLock._release_fn()

    def leak(self) -> bool:
        owns = self.owns_lock
        self.owns_lock = False
        return owns


__all__ = ["RebaseLock"]
