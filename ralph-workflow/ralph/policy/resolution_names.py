"""The one name of the integration-resolution drain and phase.

A leaf module so the policy, recovery, display and pipeline layers can all share
it without importing each other; every other definition of this name is an alias
of :data:`RESOLUTION_DRAIN_NAME`.
"""

from __future__ import annotations

from typing import Final

__all__ = ["RESOLUTION_DRAIN_NAME"]

RESOLUTION_DRAIN_NAME: Final[str] = "rebase_conflict_resolution"
