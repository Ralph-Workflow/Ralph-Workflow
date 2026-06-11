"""PhaseCloseOptions dataclass.

Internal leaf module (wt-007-consolidate-display). Re-exports
:class:`PhaseCloseOptions` from the previous
``ralph.display.plain_renderer._phase_close_options`` location.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.display._phase_close_counters import _PhaseCloseCounters
    from ralph.display.phase_status import PhaseIterationContext


@dataclass(frozen=True)
class PhaseCloseOptions:
    """Optional parameters for emit_phase_close."""

    phase_role: str | None = None
    iteration_context: PhaseIterationContext | None = None
    exit_trigger: str | None = None
    counter_overrides: _PhaseCloseCounters | None = None
