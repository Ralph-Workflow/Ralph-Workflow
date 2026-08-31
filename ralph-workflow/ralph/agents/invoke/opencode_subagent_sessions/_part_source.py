"""Where child-session parts come from, and where OpenCode keeps its store."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._child_part import OpenCodeChildPart


class OpenCodeChildPartSource(Protocol):
    """Where child-session parts come from; the SQLite store in production."""

    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        """Return parts of every descendant of ``parent_session_id`` updated at or after ``since_ms``."""
        ...

    def close(self) -> None:
        """Release any handle the source holds."""
        ...


def default_opencode_db_path(
    env_getter: Callable[[str], str | None] = os.environ.get,
) -> Path | None:
    """Return OpenCode's SQLite store path the way the 1.18.x binary resolves it.

    The data directory is ``$XDG_DATA_HOME/opencode`` (default
    ``~/.local/share``, on macOS too). ``OPENCODE_DB`` overrides the store:
    ``:memory:`` means there is nothing on disk to read (``None``), an
    absolute path is used as-is, and a relative name is resolved under the
    data directory. Release channels store in ``opencode.db``; other
    channels use ``opencode-<channel>.db``, so when the release store is
    absent the newest channel store beside it is used instead.
    """
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
    # filesystem-read-ok: existence probes of OpenCode's own data dir outside the workspace
    if release_store.is_file():
        return release_store
    try:
        # filesystem-read-ok: channel-store discovery in OpenCode's own data dir
        channel_stores = [path for path in data_dir.glob("opencode-*.db") if path.is_file()]
    except OSError:
        return release_store
    if not channel_stores:
        return release_store
    return max(channel_stores, key=_modified_at)


def _modified_at(path: Path) -> float:
    # filesystem-read-ok: mtime of a candidate OpenCode channel store in OpenCode's own data dir
    return float(path.stat().st_mtime)


__all__ = ["OpenCodeChildPartSource", "default_opencode_db_path"]
