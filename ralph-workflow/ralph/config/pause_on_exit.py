"""Pause behavior enum for process exit."""

from enum import StrEnum


class PauseOnExit(StrEnum):
    """Pause behavior before process exit.

    Attributes:
        AUTO: Pause only on standalone failure
        ALWAYS: Always pause before exit
        NEVER: Never pause before exit
    """

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"
