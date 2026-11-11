
from __future__ import annotations

from ralph.config.enums import Verbosity

VERBOSITY_RANK = {
    Verbosity.QUIET: 0,
    Verbosity.NORMAL: 1,
    Verbosity.VERBOSE: 2,
    Verbosity.FULL: 3,
    Verbosity.DEBUG: 4,
}


def verbosity_rank(verbosity: Verbosity) -> int:
    return VERBOSITY_RANK.get(verbosity, VERBOSITY_RANK[Verbosity.VERBOSE])


def normalize_verbosity(value: Verbosity | int | None) -> Verbosity:
    if isinstance(value, Verbosity):
        return value
    if isinstance(value, int):
        for vb, rank in VERBOSITY_RANK.items():
            if rank == value:
                return vb
    return Verbosity.VERBOSE
