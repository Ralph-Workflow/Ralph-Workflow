
from __future__ import annotations

from packaging.version import InvalidVersion, Version


def is_newer(current: str, latest: str) -> bool:
    try:
        return Version(latest) > Version(current)
    except (InvalidVersion, TypeError):
        return False
