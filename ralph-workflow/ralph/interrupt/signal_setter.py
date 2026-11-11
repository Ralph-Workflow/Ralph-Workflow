
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ralph.interrupt.signal_handler import SignalHandler


class SignalSetter(Protocol):

    def __call__(self, _signalnum: int, handler: SignalHandler, /) -> SignalHandler: ...


__all__ = ["SignalSetter"]
