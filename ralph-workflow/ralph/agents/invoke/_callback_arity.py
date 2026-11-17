from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from types import CodeType

    from ralph.agents.invoke._workspace import WorkspaceEventCallback


@runtime_checkable
class _HasCode(Protocol):
    __code__: CodeType


@runtime_checkable
class _HasFunc(Protocol):
    __func__: object


@runtime_checkable
class _HasDefaults(Protocol):
    __defaults__: tuple[object, ...] | None


@runtime_checkable
class _HasSelf(Protocol):
    __self__: object


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
    if isinstance(callback, _HasCode):
        return callback.__code__
    if isinstance(callback, _HasFunc):
        func = callback.__func__
        if isinstance(func, _HasCode):
            return func.__code__
    raise TypeError("callback has no code object")


def _defaults_tuple(callback: object) -> tuple[object, ...] | None:
    if isinstance(callback, _HasDefaults):
        return callback.__defaults__
    if isinstance(callback, _HasFunc):
        func = callback.__func__
        if isinstance(func, _HasDefaults):
            return func.__defaults__
    return None


def _required_positional_count(callback: object) -> tuple[int, int, bool]:
    code = _callable_code(callback)
    positional = code.co_argcount
    defaults_obj = _defaults_tuple(callback)
    default_count = len(defaults_obj) if defaults_obj is not None else 0
    varargs = bool(code.co_flags & 0x04)
    method_self = (
        1 if isinstance(callback, _HasSelf) and callback.__self__ is not None else 0
    )
    return positional - method_self, default_count, varargs


def _can_bind_required(shape: tuple[int, int, bool], n: int) -> bool:
    positional, default_count, varargs = shape
    required = max(0, positional - default_count)
    if n < required:
        return False
    if n <= positional:
        return True
    return varargs
