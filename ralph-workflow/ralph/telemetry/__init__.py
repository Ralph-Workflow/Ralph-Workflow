"""Anonymous telemetry for Ralph Workflow — error reporting and performance monitoring.

No personally identifiable information is collected. See _user_identity.py and _sentry.py.
"""

from ralph.telemetry._sentry import (
    ScrubPolicy,
    init_sentry,
    is_telemetry_disabled,
    scrub_event,
    scrub_obj,
    scrub_object,
)

__all__ = [
    "ScrubPolicy",
    "init_sentry",
    "is_telemetry_disabled",
    "scrub_event",
    "scrub_obj",
    "scrub_object",
]
