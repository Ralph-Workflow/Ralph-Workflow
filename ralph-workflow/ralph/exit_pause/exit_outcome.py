
from __future__ import annotations

from enum import StrEnum


class ExitOutcome(StrEnum):

    SUCCESS = "success"
    FAILURE = "failure"
    INTERRUPTED = "interrupted"
