"""ProcessManagerPolicy dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessManagerPolicy:
    default_grace_period_s: float = 5.0
    kill_followup_timeout_s: float = 2.0
    log_events: bool = True
    terminal_history_limit: int = 256
    purge_on_init: bool = False
    # Background zombie reaper settings. When enable_zombie_reaper is True,
    # ProcessManager starts a daemon thread on the first tracked process that
    # periodically reconciles stale tracking entries (PIDs no longer alive at
    # the OS level) and zombie records. Set to False in tests so unit tests do
    # not create background threads. Production default remains True.
    enable_zombie_reaper: bool = True
    zombie_reaper_interval_s: float = 5.0
