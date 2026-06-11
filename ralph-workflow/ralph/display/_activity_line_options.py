"""Optional parameter group for emit_activity_line.

Internal leaf module (wt-007-consolidate-display). Re-exports
:class:`ActivityLineOptions` from the previous
``ralph.display.plain_renderer._activity_line_options`` location.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActivityLineOptions:
    """Optional parameter group for emit_activity_line."""

    condensed_ref: str | None = None
    condensed_flag: bool = False
    summary_line: str | None = None
    ai_summary_line: str | None = None
    tool_signature: tuple[str, str] | None = None
