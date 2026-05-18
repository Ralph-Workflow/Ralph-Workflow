"""PostExitVerdict enum for post-exit watchdog results."""

from __future__ import annotations

from enum import StrEnum


class PostExitVerdict(StrEnum):
    """Result of a PostExitWatchdog wait method."""

    CONTINUE = "continue"
    FIRE_PROCESS_EXIT_HANG = "fire_process_exit_hang"

    SIGNALS_PRESENT = "signals_present"
    CHILDREN_ACTIVE = "children_active"
    QUIESCED_NO_SIGNALS = "quiesced_no_signals"
    FIRE_DESCENDANT_HANG = "fire_descendant_hang"
