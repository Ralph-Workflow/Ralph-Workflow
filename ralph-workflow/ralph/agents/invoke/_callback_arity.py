from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import CodeType

    from ralph.agents.invoke._workspace import WorkspaceEventCallback

VALID_CALLBACK_ARITIES: frozenset[int] = frozenset({0, 2})

TWO_ARG_ARITY: int = 2


def callback_arity(callback: WorkspaceEventCallback) -> int:
    try:
        required = _required_positional_count(callback)
    except (TypeError, ValueError, AttributeError):
        msg = (
            f"WorkspaceMonitor on_event callback has an uninspectable signature;"
            f" expected 0 or 2 required positional args, got callback of type"
            f" {type(callback).__name__}"
        )
        raise ValueError(msg) from None
    can_bind_zero = _can_bind_required(required, 0)
    can_bind_two = _can_bind_required(required, 2)
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


def _callable_code(callback: object) -> CodeType:
    code = getattr(callback, "__code__", None)
    if code is not None:
        return code
    func = getattr(callback, "__func__", None)
    code = getattr(func, "__code__", None) if func is not None else None
    if code is not None:
        return code
    raise TypeError("callback has no code object")


def _required_positional_count(callback: object) -> tuple[int, int, bool]:
    code = _callable_code(callback)
    positional = int(code.co_argcount)
    defaults_obj = getattr(callback, "__defaults__", None)
    if defaults_obj is None:
        func = getattr(callback, "__func__", None)
        defaults_obj = getattr(func, "__defaults__", None) if func is not None else None
    default_count = len(defaults_obj) if defaults_obj is not None else 0
    varargs = bool(code.co_flags & 0x04)
    method_self = 1 if getattr(callback, "__self__", None) is not None else 0
    return positional - method_self, default_count, varargs


def _can_bind_required(shape: tuple[int, int, bool], n: int) -> bool:
    positional, default_count, varargs = shape
    required = max(0, positional - default_count)
    if n < required:
        return False
    if n <= positional:
        return True
    return varargs
