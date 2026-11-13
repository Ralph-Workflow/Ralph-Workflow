from __future__ import annotations

from enum import StrEnum

__all__ = ("LadderEffectKind",)


class LadderEffectKind(StrEnum):
    RUN_RUNG = "run_rung"
    EMIT_ESCALATION = "emit_escalation"
    EMIT_TERMINAL = "emit_terminal"
    RECORD_TERMINAL_MARKER = "record_terminal_marker"
