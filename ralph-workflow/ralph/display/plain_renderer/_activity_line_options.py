"""Optional parameter group for emit_activity_line."""

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
