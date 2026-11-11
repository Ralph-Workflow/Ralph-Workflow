
from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["operator_command_for_paths"]


def operator_command_for_paths(paths: Sequence[str]) -> str:
    if not paths:
        return ""
    quoted_paths = " ".join(shlex.quote(path) for path in paths)
    return f"git add -- {quoted_paths}"
