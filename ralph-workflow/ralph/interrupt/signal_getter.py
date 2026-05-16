"""Protocol for signal getter callables."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ralph.interrupt.signal_handler import SignalHandler


class SignalGetter(Protocol):
    """Protocol for ``signal.getsignal``-compatible callables."""

    def __call__(self, _signalnum: int, /) -> SignalHandler: ...


__all__ = ["SignalGetter"]
