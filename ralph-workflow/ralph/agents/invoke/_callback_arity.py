"""How many positional arguments a workspace-event callback really takes.

Split out of :mod:`ralph.agents.invoke._workspace`: deciding a
callback's effective arity is a question about signatures, not about
watching a workspace, and it is the whole reason the monitor can accept
both the legacy 0-arg binding and the 2-arg ``(kind, weight)`` one.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only, so the cycle with the module this was split from
    # never exists at runtime.
    from ralph.agents.invoke._workspace import WorkspaceEventCallback

#: Arities the monitor accepts: the legacy 0-arg binding and the
#: production 2-arg ``(kind, weight)`` one.
VALID_CALLBACK_ARITIES: frozenset[int] = frozenset({0, 2})

#: The production binding's arity.
TWO_ARG_ARITY: int = 2


def callback_arity(callback: WorkspaceEventCallback) -> int:
    """Return the number of required positional parameters of ``callback``.

    Used by ``WorkspaceMonitor.__post_init__`` to enforce the 0-arg
    or 2-arg contract. ``inspect.signature`` already follows
    ``functools.partial`` and ``functools.wraps`` chains, and
    automatically excludes the bound ``self`` parameter for bound
    methods, so the returned ``signature.parameters`` map is
    authoritative for the effective positional arity. A callback
    with ``*args`` or ``**kwargs`` has a non-finite arity; the
    classifier counts the explicit positional slots before ``*args``
    and treats the result as the effective arity.

    Returns:
        Number of required positional parameters as observed by the
        caller (excluding ``self`` for bound methods).
    """
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
    """Return True iff ``signature_obj`` accepts exactly ``n`` positional args.

    A variadic-only signature (e.g. ``*args, **kwargs``) accepts any
    number of args, so both ``n=0`` and ``n=2`` return True. The
    arity check in ``callback_arity`` rejects signatures where
    both bind successfully, so a variadic-only callback is not
    mistakenly classified as 0-arg or 2-arg.

    Used to avoid touching ``Parameter.kind`` (which is typed as
    ``Any`` in the upstream typeshed stub) and the
    ``Parameter.empty`` sentinel (also ``Any``-typed) so the
    mypy ``disallow_any_expr`` check does not flag the
    ``inspect.Parameter``-typed expressions.
    """
    args: tuple[object, ...] = tuple(object() for _ in range(n))
    try:
        signature_obj.bind(*args)
    except TypeError:
        return False
    return True
