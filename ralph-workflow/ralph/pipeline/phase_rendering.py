"""Verbosity ranking and normalization helpers for the pipeline runner."""

from __future__ import annotations

from ralph.config.enums import Verbosity

VERBOSITY_RANK: dict[Verbosity, int] = {
    Verbosity.QUIET: 0,
    Verbosity.NORMAL: 1,
    Verbosity.VERBOSE: 2,
    Verbosity.FULL: 3,
    Verbosity.DEBUG: 4,
}


def verbosity_rank(verbosity: Verbosity) -> int:
    """Return a numeric rank for a Verbosity enum value (QUIET=0 .. DEBUG=4)."""
    return VERBOSITY_RANK.get(verbosity, VERBOSITY_RANK[Verbosity.VERBOSE])


def normalize_verbosity(value: Verbosity | int | None) -> Verbosity:
    """Coerce a Verbosity enum, integer rank, or None into a Verbosity value.

    The legacy ``GeneralConfig.verbosity`` field is an integer (0-4); the new
    CLI surface is the ``Verbosity`` StrEnum. This helper accepts either and
    falls back to ``Verbosity.VERBOSE`` for unknown / unset inputs.
    """
    if isinstance(value, Verbosity):
        return value
    if isinstance(value, int):
        for vb, rank in VERBOSITY_RANK.items():
            if rank == value:
                return vb
    return Verbosity.VERBOSE
