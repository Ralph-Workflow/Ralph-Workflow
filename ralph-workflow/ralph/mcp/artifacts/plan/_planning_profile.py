"""Free-form planning-profile hint for DesignSection.

The profile is a descriptive preset hint with no downstream consumer, so
any non-empty string is accepted. The names ``strict`` and ``balanced``
are recognized presets that bias-fill missing design sub-sections (see
``DesignSection``); other values are kept verbatim.
"""

from __future__ import annotations

type PlanningProfile = str

__all__ = ["PlanningProfile"]
