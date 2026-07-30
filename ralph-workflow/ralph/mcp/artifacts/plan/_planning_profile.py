"""Free-form planning-profile hint for DesignSection.

The profile has no downstream consumer, so any non-empty string is accepted
and kept verbatim. It never injects design sub-sections or execution rules.
"""

from __future__ import annotations

type PlanningProfile = str

__all__ = ["PlanningProfile"]
