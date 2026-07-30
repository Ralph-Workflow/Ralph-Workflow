"""Persisted state and throttling for the update nagger.

A tiny JSON cache under ``$XDG_CACHE_HOME/ralph-workflow/version-check.json``
records when PyPI was last consulted and what it reported, so the network is hit
at most once per TTL window. A missing or corrupt cache is treated as empty
state and simply overwritten on the next successful refresh.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_TTL_SECONDS = 24 * 60 * 60
_CACHE_RELATIVE = Path("ralph-workflow") / "version-check.json"


@dataclass(frozen=True)
class VersionCheckState:
    """Last-known result of a PyPI version check."""

    last_checked: float
    latest_version: str | None


def cache_path(environ: Mapping[str, str]) -> Path:
    """Resolve the cache file path, honoring ``XDG_CACHE_HOME``."""
    xdg = environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / _CACHE_RELATIVE


def load_state(path: Path) -> VersionCheckState | None:
    """Load persisted state, returning ``None`` for missing or corrupt files."""
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


def save_state(path: Path, state: VersionCheckState) -> None:
    """Persist ``state`` to ``path``; filesystem errors are swallowed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        document: dict[str, object] = {
            "last_checked": state.last_checked,
            "latest_version": state.latest_version,
        }
        path.write_text(json.dumps(document), encoding="utf-8")
    except OSError:
        pass


def is_refresh_due(
    state: VersionCheckState | None, now: float, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> bool:
    """Return True when the cache is absent or older than ``ttl_seconds``."""
    if state is None:
        return True
    return (now - state.last_checked) >= ttl_seconds
