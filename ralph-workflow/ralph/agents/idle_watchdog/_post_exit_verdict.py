
from __future__ import annotations

from enum import StrEnum


class PostExitVerdict(StrEnum):

    CONTINUE = "continue"
    FIRE_PROCESS_EXIT_HANG = "fire_process_exit_hang"

    SIGNALS_PRESENT = "signals_present"
    CHILDREN_ACTIVE = "children_active"
    QUIESCED_NO_SIGNALS = "quiesced_no_signals"
    FIRE_DESCENDANT_HANG = "fire_descendant_hang"
