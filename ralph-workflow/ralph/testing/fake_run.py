"""FakeRun: seeded replay script for a single parallel work unit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class FakeRun:
    """Seeded replay script for a single parallel work unit."""

    outputs: list[str]
    exit_code: int
    duration_ms: int
    raise_on_start: Exception | None = None
    side_effect: Callable[[], None] | None = field(default=None)
