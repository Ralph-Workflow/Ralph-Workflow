from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from ralph.update_check.environment import InstallInfo
    from ralph.update_check.state import VersionCheckState


@dataclass(frozen=True)
class UpdateCheckDeps:
    current_version: str
    environ: Mapping[str, str]
    now: Callable[[], float]
    is_disabled: Callable[[Mapping[str, str]], bool]
    cache_path: Callable[[Mapping[str, str]], Path]
    load_state: Callable[[Path], VersionCheckState | None]
    save_state: Callable[[Path, VersionCheckState], None]
    is_refresh_due: Callable[[VersionCheckState | None, float], bool]
    is_newer: Callable[[str, str], bool]
    detect_install: Callable[[], InstallInfo]
    fetch_latest: Callable[[], str | None]
    spawn: Callable[[Callable[[], None]], None]
