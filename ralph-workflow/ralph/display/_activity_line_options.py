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
    # S-14 (wt-028-display P1 / AC-04): the parsed event's metadata
    # is forwarded to the record seam so ``_derive_severity`` can
    # inspect outcome flags (``exit_code`` etc.) and stamp
    # ``severity=error`` for failed tool results. ``None`` preserves
    # the pre-S-14 outcome-blind behavior. The metadata payload is a
    # plain string-keyed map of arbitrary scalar values from the
    # parsers; we keep the type wide and let consumers narrow at use.
    activity_metadata: dict[str, object] | None = None
