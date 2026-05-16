"""Verbosity level enum for Ralph output."""

from enum import StrEnum


class Verbosity(StrEnum):
    """Verbosity level for Ralph output.

    Attributes:
        QUIET: Minimal output (errors only)
        NORMAL: Default verbosity level
        VERBOSE: More detailed output
        FULL: Full output with all details
        DEBUG: Debug-level output for troubleshooting
    """

    QUIET = "quiet"
    NORMAL = "normal"
    VERBOSE = "verbose"
    FULL = "full"
    DEBUG = "debug"
