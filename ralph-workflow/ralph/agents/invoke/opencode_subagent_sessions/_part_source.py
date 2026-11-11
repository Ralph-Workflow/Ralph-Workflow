
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._child_part import OpenCodeChildPart


class OpenCodeChildPartSource(Protocol):

    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        ...

    def close(self) -> None:
        ...


def default_opencode_db_path(
    env_getter: Callable[[str], str | None] = os.environ.get,
) -> Path | None:
    xdg_data_home = env_getter("XDG_DATA_HOME")
    home = env_getter("HOME")
    data_root = (
        Path(xdg_data_home)
        if xdg_data_home
        else Path(home) / ".local" / "share"
        if home
        else Path.home() / ".local" / "share"
    )
    data_dir = data_root / "opencode"
    override = env_getter("OPENCODE_DB")
    if override:
        if override == ":memory:":
            return None
        override_path = Path(override)
        return override_path if override_path.is_absolute() else data_dir / override_path
    release_store = data_dir / "opencode.db"
    if release_store.is_file():
        return release_store
    try:
        channel_stores = [path for path in data_dir.glob("opencode-*.db") if path.is_file()]
    except OSError:
        return release_store
    if not channel_stores:
        return release_store
    return max(channel_stores, key=_modified_at)


def _modified_at(path: Path) -> float:
    return float(path.stat().st_mtime)


__all__ = ["OpenCodeChildPartSource", "default_opencode_db_path"]
