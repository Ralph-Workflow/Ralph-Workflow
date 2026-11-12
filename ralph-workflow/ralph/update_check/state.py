from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_TTL_SECONDS = 24 * 60 * 60
_CACHE_RELATIVE = Path("ralph-workflow") / "version-check.json"


@dataclass(frozen=True)
class VersionCheckState:
    last_checked: float
    latest_version: str | None


def cache_path(environ: Mapping[str, str]) -> Path:
    xdg = environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / _CACHE_RELATIVE


def load_state(path: Path) -> VersionCheckState | None:
    try:
        parsed = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    raw = cast("dict[str, object]", parsed)
    last_checked = raw.get("last_checked")
    if not isinstance(last_checked, (int, float)) or isinstance(last_checked, bool):
        return None
    latest_version = raw.get("latest_version")
    if not isinstance(latest_version, str):
        latest_version = None
    return VersionCheckState(last_checked=float(last_checked), latest_version=latest_version)


def save_state(
    path: Path,
    state: VersionCheckState,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    try:
        document: dict[str, object] = {
            "last_checked": state.last_checked,
            "latest_version": state.latest_version,
        }
        write_text_if_changed(
            backend,
            path,
            json.dumps(document),
            encoding="utf-8",
            prepare_write=lambda: backend.mkdir(path.parent, parents=True, exist_ok=True),
        )
    except OSError:
        pass


def is_refresh_due(
    state: VersionCheckState | None, now: float, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> bool:
    if state is None:
        return True
    return (now - state.last_checked) >= ttl_seconds
