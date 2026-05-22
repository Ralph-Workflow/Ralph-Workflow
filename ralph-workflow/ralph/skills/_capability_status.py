"""Capability status enum - backward compatibility re-export.

New code should import from ralph.skills._state:
    from ralph.skills._state import CapabilityStatus
"""

from __future__ annotations

from ralph.skills._state import CapabilityStatus

__all__ = ["CapabilityStatus"]
