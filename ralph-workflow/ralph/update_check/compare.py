"""PEP 440 version comparison for the update nagger.

Kept deliberately tiny and total: any unparseable input yields ``False`` so a
malformed local or remote version can never crash a run or trigger a false nag.
"""

from __future__ import annotations

from packaging.version import InvalidVersion, Version


def is_newer(current: str, latest: str) -> bool:
    """Return True when ``latest`` is a strictly newer release than ``current``.

    Comparison follows PEP 440 ordering (so a pre-release is not treated as
    newer than the matching final). Any parse failure returns ``False``.
    """
    try:
        return Version(latest) > Version(current)
    except (InvalidVersion, TypeError):
        return False
