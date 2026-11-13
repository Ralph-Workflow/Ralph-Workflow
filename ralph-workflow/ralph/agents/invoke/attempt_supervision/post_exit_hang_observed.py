from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog import PostExitVerdict


@dataclass(frozen=True)
class PostExitHangObserved:
    verdict: PostExitVerdict
