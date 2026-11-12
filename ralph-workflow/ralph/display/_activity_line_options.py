from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActivityLineOptions:
    condensed_ref: str | None = None
    condensed_flag: bool = False
    summary_line: str | None = None
    ai_summary_line: str | None = None
    tool_signature: tuple[str, str] | None = None
    activity_metadata: dict[str, object] | None = None
    indent_level: int = 0
    grouping_role: str = "agent_text"
    record_body: str | None = None
