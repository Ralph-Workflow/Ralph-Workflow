"""Shared numeric defaults for agent timeout policy and child-liveness configuration.

These constants are the single source of truth for all timeout and child-liveness
numeric defaults. They are imported by ``ralph.agents.idle_watchdog.TimeoutPolicy``
(dataclass field defaults), ``ralph.agents.invoke`` (child-liveness TTL module-level
constants), and ``ralph.config.models.GeneralConfig`` (field defaults).

Changing a constant here automatically propagates to all three layers so they
cannot drift independently.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Idle / session timeout defaults
# ---------------------------------------------------------------------------

#: Default idle timeout: maximum seconds without agent output before firing.
IDLE_TIMEOUT_SECONDS: float = 300.0

#: Default drain window duration before firing NO_OUTPUT_DEADLINE.
DRAIN_WINDOW_SECONDS: float = 0.5

#: Default hard ceiling on cumulative WAITING_ON_CHILD time.
MAX_WAITING_ON_CHILD_SECONDS: float = 1800.0

#: Default absolute session wall-clock ceiling. None means disabled.
MAX_SESSION_SECONDS: float | None = None

#: Default poll interval for the read loop.
IDLE_POLL_INTERVAL_SECONDS: float = 0.05

#: Default grace window after parent exits normally.
PARENT_EXIT_GRACE_SECONDS: float = 5.0

#: Default ceiling for descendant-wait after parent exits.
DESCENDANT_WAIT_TIMEOUT_SECONDS: float = 30.0

#: Default poll interval for descendant-wait / process-exit-wait loops.
DESCENDANT_WAIT_POLL_SECONDS: float = 0.5

#: Default ceiling for waiting on subprocess exit after stdout closes.
PROCESS_EXIT_WAIT_SECONDS: float = 30.0

#: Default cadence for WAITING_ON_CHILD periodic status events.
WAITING_STATUS_INTERVAL_SECONDS: float = 30.0

#: Default suspicion threshold: cumulative WAITING time before SUSPECTED_FROZEN event.
#: None disables suspicion.
SUSPECT_WAITING_ON_CHILD_SECONDS: float | None = 600.0

#: Default no-progress ceiling: shorter WAITING ceiling when child is alive but not
#: making forward progress (heartbeat-only, stale-label, or OS-descendant-only).
#: None disables the no-progress ceiling.
MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS: float | None = 600.0

# ---------------------------------------------------------------------------
# Child-liveness TTL defaults
# ---------------------------------------------------------------------------

#: Maximum seconds since last child progress signal before treated as not-progressing.
CHILD_PROGRESS_TTL_SECONDS: float = 45.0

#: Maximum seconds since last child heartbeat before heartbeat is stale.
CHILD_HEARTBEAT_TTL_SECONDS: float = 15.0

#: Grace period during which a child label persists after evidence goes stale.
CHILD_STALE_LABEL_TTL_SECONDS: float = 10.0

#: Reconciliation window after stdout EOF for late terminal acks.
CHILD_EXIT_RECONCILE_SECONDS: float = 5.0
