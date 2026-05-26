"""TTL-based recheck policy for capability health state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.pydantic_compat import ConfigDict, RalphBaseModel
from ralph.skills._capability_status import CapabilityStatus

if TYPE_CHECKING:
    from ralph.skills._capability_entry import CapabilityEntry


class RecheckPolicy(RalphBaseModel):
    """Recheck timing policy for capability health probes."""

    model_config = ConfigDict(frozen=True)

    healthy_recheck_hours: float = 24.0
    failed_recheck_hours: float = 1.0
    always_recheck_if_not_installed: bool = True


DEFAULT_POLICY: RecheckPolicy = RecheckPolicy()


def _hours_since_iso(iso_str: str) -> float | None:
    """Return hours since an ISO timestamp, or None if the string is empty/invalid."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        delta = datetime.now(tz=UTC) - dt
        return delta.total_seconds() / 3600.0
    except (ValueError, OverflowError):
        return None


def needs_recheck(entry: CapabilityEntry, policy: RecheckPolicy | None = None) -> bool:
    """Return True if a capability entry should be rechecked based on TTL policy."""
    p = policy if policy is not None else DEFAULT_POLICY
    s = entry.status
    if s == CapabilityStatus.NOT_INSTALLED:
        return p.always_recheck_if_not_installed
    if s in {CapabilityStatus.NEEDS_REPAIR, CapabilityStatus.CONFIGURED_UNREACHABLE}:
        hours = _hours_since_iso(entry.last_check_fail_iso)
        return hours is None or hours >= p.failed_recheck_hours
    hours = _hours_since_iso(entry.last_check_ok_iso)
    return hours is None or hours >= p.healthy_recheck_hours


__all__ = ["DEFAULT_POLICY", "RecheckPolicy", "needs_recheck"]
