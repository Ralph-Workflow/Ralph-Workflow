"""Policy constants for the process manager.

Governs subprocess lifecycle: graceful termination timeouts, event logging,
history limits, zombie reaping, and listener-subscription backpressure.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessManagerPolicy:
    """Immutable configuration for a ``ProcessManager`` instance."""

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
    # wt-024 M8 (AC-09): FIFO cap on ``ProcessManager._listeners``. The
    # default of 64 is well above the steady-state count of ~3 (the
    # permanent loguru_event_listener + the PTY reader + the process
    # reader) so production listeners are never evicted; the cap only
    # triggers on a leaked subscription. ``register_listener`` evicts
    # the OLDEST listener (dict insertion order) when the cap is
    # exceeded, so the leak surface is bounded.
    max_listeners: int = 64
