"""Anonymous telemetry for Ralph Workflow — error reporting and performance monitoring.

No personally identifiable information is collected. See _user_identity.py and _sentry.py.

:func:`reset_telemetry_state` is the public operational reset for the live
session handle: it returns the session to its pristine defaults so a caller
that reuses the process (a second session in the same interpreter) starts
from a known state instead of inheriting the previous session's outcome,
wall-clock buckets, or aggregated counters.
"""

from ralph.telemetry._sentry import (
    ScrubPolicy,
    init_sentry,
    is_telemetry_disabled,
    reset_telemetry_state,
    scrub_event,
    scrub_obj,
    scrub_object,
)

__all__ = [
    "ScrubPolicy",
    "init_sentry",
    "is_telemetry_disabled",
    "reset_telemetry_state",
    "scrub_event",
    "scrub_obj",
    "scrub_object",
]
