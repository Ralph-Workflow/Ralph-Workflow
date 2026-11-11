
from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.invoke._workspace import WorkspaceEventCallback

VALID_CALLBACK_ARITIES: frozenset[int] = frozenset({0, 2})

TWO_ARG_ARITY: int = 2


def callback_arity(callback: WorkspaceEventCallback) -> int:
    try:
        signature_obj: inspect.Signature = inspect.signature(callback)
    except (TypeError, ValueError):
        msg = (
            f"WorkspaceMonitor on_event callback has an uninspectable signature;"
            f" expected 0 or 2 required positional args, got callback of type"
            f" {type(callback).__name__}"
        )
        raise ValueError(msg) from None
    can_bind_zero = _can_bind_n(signature_obj, 0)
    can_bind_two = _can_bind_n(signature_obj, 2)
    if can_bind_zero and not can_bind_two:
        return 0
    if can_bind_two and not can_bind_zero:
        return 2
    msg = (
        f"WorkspaceMonitor on_event callback has the wrong arity;"
        f" expected exactly 0 or 2 required positional args, got"
        f" callback of type {type(callback).__name__}"
    )
    raise ValueError(msg)


def _can_bind_n(signature_obj: inspect.Signature, n: int) -> bool:
    args: tuple[object, ...] = tuple(object() for _ in range(n))
    try:
        signature_obj.bind(*args)
    except TypeError:
        return False
    return True
