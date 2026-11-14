from __future__ import annotations

from enum import StrEnum


class AgentMode(StrEnum):
    EDITS_AND_STAGES = "edits_and_stages"
    EDITS_NEVER_STAGES = "edits_never_stages"
    LEAVES_MARKER = "leaves_marker"
