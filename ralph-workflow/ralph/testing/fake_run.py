from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class FakeRun:
    outputs: list[str]
    exit_code: int
    duration_ms: int
    raise_on_start: Exception | None = None
    side_effect: Callable[[], None] | None = field(default=None)
