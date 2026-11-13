from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog import WatchdogFireReason


@dataclass(frozen=True)
class SilenceObserved:
    fire_reason: WatchdogFireReason | str | None
