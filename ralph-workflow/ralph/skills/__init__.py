from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.skills._content import get_skill_content

if TYPE_CHECKING:
    import ralph.skills._state_store as state_store

__all__ = ["get_skill_content", "state_store"]


def __getattr__(name: str) -> object:
    if name != "state_store":
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)

    from ralph.skills import _state_store as module

    return module
