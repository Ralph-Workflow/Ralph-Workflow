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
    # S-12 (wt-028-display P1 / AC-07 / DA-002): the canonical
    # ``PresentedEntry`` hierarchy data flows through to the live
    # log so the hanging-indent continuation column reflects the
    # entry's structural role. ``indent_level`` adds N copies of
    # the badge column (two spaces per level) so a tool_result
    # hangs one level under its call, and a reasoning entry reads
    # as one subordinated passage. ``grouping_role`` is the
    # semantic label (e.g. ``tool_result``, ``reasoning``,
    # ``phase_header``) carried for downstream consumers; the live
    # log uses it to apply the canonical indent without forking
    # the data per consumer.
    indent_level: int = 0
    grouping_role: str = "agent_text"
