
from __future__ import annotations

from enum import StrEnum


class PauseOnExitMode(StrEnum):

    NEVER = "never"
    ALWAYS = "always"
    AUTO = "auto"
