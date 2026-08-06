"""E-1's render-frequency classification (PLAN.md Section E).

Split from :mod:`ralph.display._palette` so that module keeps a single
top-level class (``RoleAnchor``) per the repo's one-class-per-file
structure policy (``ralph.testing.audit_repo_structure``).
"""

from __future__ import annotations

from enum import IntEnum


class FrequencyTier(IntEnum):
    """E-1's render-frequency classification, ordered least- to
    most-saturated by tier number."""

    FIELD = 1
    STRUCTURE = 2
    EVENT = 3
    ALARM = 4


__all__ = ["FrequencyTier"]
