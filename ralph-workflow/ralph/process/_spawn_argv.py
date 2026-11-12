from __future__ import annotations

import os
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

NUL_CHARACTER = "\x00"


def _strip_nul(value: str) -> tuple[str, int]:
    removed = value.count(NUL_CHARACTER)
    if removed == 0:
        return value, 0
    return value.replace(NUL_CHARACTER, ""), removed


def _scope(label: str | None) -> str:
    return f" for {label}" if label else ""


def sanitize_spawn_command(
    command: Sequence[str],
    *,
    label: str | None = None,
) -> tuple[str, ...]:
    cleaned: list[str] = []
    sites: list[str] = []
    for index, token in enumerate(command):
        if index == 0:
            cleaned.append(token)
            continue
        try:
            value, removed = _strip_nul(os.fspath(token))
        except TypeError:
            cleaned.append(token)
            continue
        cleaned.append(value)
        if removed:
            sites.append(f"argv[{index}]: {removed}")
    if sites:
        logger.warning(
            f"Spawn{_scope(label)}: stripped NUL characters from the command "
            f"({', '.join(sites)}) — execve cannot carry an embedded NUL"
        )
    return tuple(cleaned)
