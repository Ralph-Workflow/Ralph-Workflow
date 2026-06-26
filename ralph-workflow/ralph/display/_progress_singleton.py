"""ProgressSingleton — singleton wrapper for RalphProgress keyed by console identity."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from typing import Protocol

    from ralph.display.context import DisplayContext
    from ralph.display.progress import RalphProgress

    class _ProgressFactory(Protocol):
        def __call__(self, *, context: DisplayContext) -> RalphProgress: ...


class ProgressSingleton:
    """Singleton wrapper for RalphProgress, keyed by context console id.

    Note:
        Callers must NOT re-create DisplayContext per phase; they must use
        refreshed() or pass the same context. Different contexts (identified
        by console identity) yield separate RalphProgress instances.
    """

    # bounded-accumulator-ok: keyed by id(console); bounded by live
    # console count (1 in practice)
    instances: ClassVar[dict[int, RalphProgress]] = {}  # bounded-accumulator-ok: id(console)
    _factory: ClassVar[_ProgressFactory | None] = None

    @classmethod
    def get(cls, context: DisplayContext) -> RalphProgress:
        """Get the RalphProgress singleton for the given context."""
        if cls._factory is None:
            raise RuntimeError("ProgressSingleton._factory has not been injected")
        key = id(context.console)
        if key not in cls.instances:
            cls.instances[key] = cls._factory(context=context)
        return cls.instances[key]


__all__ = ["ProgressSingleton"]
