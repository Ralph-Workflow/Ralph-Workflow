from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.recovery.controller import RecoveryController

__all__ = ["CooldownWaitContext"]


@dataclass
class CooldownWaitContext:
    sleep: Callable[[float], None]
    controller: RecoveryController
    active_display: ParallelDisplay | None = None
    last_waiting_state_phase: str | None = None
    last_waiting_state_sig: tuple[str, str] | None = None
