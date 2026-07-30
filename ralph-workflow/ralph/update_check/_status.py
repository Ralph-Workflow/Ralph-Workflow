"""The diagnose-facing update snapshot.

Split out of :mod:`ralph.update_check` so each module owns a single public class
(repo structure policy); the package re-exports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.update_check.environment import InstallInfo


@dataclass(frozen=True)
class UpdateStatus:
    """Diagnose-friendly snapshot of the update situation."""

    current_version: str
    latest_version: str | None
    update_available: bool
    install: InstallInfo
    disabled: bool
