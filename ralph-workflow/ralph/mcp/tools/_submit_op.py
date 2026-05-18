"""Submit operation with rollback support."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class SubmitOp:
    """An ordered submit step paired with its rollback action."""

    run: Callable[[], object]
    undo: Callable[[], None]
