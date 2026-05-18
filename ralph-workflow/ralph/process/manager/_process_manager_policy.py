"""ProcessManagerPolicy dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessManagerPolicy:
    default_grace_period_s: float = 5.0
    kill_followup_timeout_s: float = 2.0
    log_events: bool = True
    terminal_history_limit: int = 256
