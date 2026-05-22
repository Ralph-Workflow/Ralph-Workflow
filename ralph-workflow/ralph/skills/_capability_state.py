"""Capability state model - backward compatibility re-export.

New code should import from ralph.skills._state:
    from ralph.skills._state import CapabilityState
"""

from __future__ annotations

from ralph.skills._state import CapabilityState

__all__ = ["CapabilityState"]
