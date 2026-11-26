from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import MutableMapping

_GREP_OPTIONS: Final[str] = "GREP_OPTIONS"
_SKIP_DEVICES: Final[str] = "-D skip"


def apply_safe_search_env(env: MutableMapping[str, str], *, platform: str = sys.platform) -> None:
    if platform == "darwin" and _GREP_OPTIONS not in env:
        env[_GREP_OPTIONS] = _SKIP_DEVICES


__all__ = ["apply_safe_search_env"]
