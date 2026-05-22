"""Capability entry model - backward compatibility re-export.

New code should import from ralph.skills._state:
    from ralph.skills._state import CapabilityEntry
"""

from __future__ annotations

from ralph.skills._state import CapabilityEntry

__all__ = ["CapabilityEntry"]
