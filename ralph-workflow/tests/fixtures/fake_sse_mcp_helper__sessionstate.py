from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import queue


@dataclass
class _SessionState:
    events: queue.Queue[bytes]
