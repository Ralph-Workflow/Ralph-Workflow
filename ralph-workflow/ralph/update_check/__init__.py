"""Update nagger: tell the user when a newer ``ralph-workflow`` release exists.

This package informs; it never mutates. Public surface:

- :func:`maybe_render_update_nag` — one-line nag at run start, cache-driven with
  a throttled background refresh so it never delays a run.
- :func:`update_status` — synchronous summary for ``ralph --diagnose``.

Every dependency is injected via :class:`UpdateCheckDeps` so the whole flow is
unit-testable without threads, network, filesystem, or a real clock.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.text import Text

from ralph import __version__
from ralph.update_check import gating as _gating
from ralph.update_check import pypi as _pypi
from ralph.update_check import state as _state
from ralph.update_check.compare import is_newer
from ralph.update_check.environment import InstallInfo, InstallKind, detect_install
from ralph.update_check.state import VersionCheckState

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.display.context import DisplayContext

__all__ = [
    "InstallInfo",
    "InstallKind",
    "UpdateCheckDeps",
    "UpdateStatus",
    "default_deps",
    "maybe_render_update_nag",
    "update_status",
]


@dataclass(frozen=True)
class UpdateStatus:
    """Diagnose-friendly snapshot of the update situation."""

    current_version: str
    latest_version: str | None
    update_available: bool
    install: InstallInfo
    disabled: bool


@dataclass(frozen=True)
class UpdateCheckDeps:
    """Injected collaborators for the update-check flow (real impls in :func:`default_deps`)."""

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


def _real_detect_install() -> InstallInfo:
    ralph_module = sys.modules.get("ralph")
    package_file = ralph_module.__file__ if ralph_module is not None else None
    frozen_attr = cast("object", getattr(sys, "frozen", False))
    return detect_install(
        package_file=package_file,
        environ=os.environ,
        is_frozen=bool(frozen_attr),
        path_exists=Path.exists,
    )


def _spawn_daemon(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


def default_deps() -> UpdateCheckDeps:
    """Wire the production implementations of every collaborator."""
    return UpdateCheckDeps(
        current_version=__version__,
        environ=os.environ,
        now=time.time,
        is_disabled=_gating.is_update_check_disabled,
        cache_path=_state.cache_path,
        load_state=_state.load_state,
        save_state=_state.save_state,
        is_refresh_due=_state.is_refresh_due,
        is_newer=is_newer,
        detect_install=_real_detect_install,
        fetch_latest=_pypi.fetch_latest_version_over_network,
        spawn=_spawn_daemon,
    )


def _refresh_cache(deps: UpdateCheckDeps, path: Path, now: float) -> None:
    """Fetch the latest version and persist it; swallow every failure."""
    try:
        latest = deps.fetch_latest()
        if latest is not None:
            deps.save_state(path, VersionCheckState(last_checked=now, latest_version=latest))
    except Exception:
        return


def maybe_render_update_nag(
    display_context: DisplayContext, *, deps: UpdateCheckDeps | None = None
) -> None:
    """Show a one-line upgrade nag when the cache reports a newer release.

    Reads only from cache for display (so run start is never blocked); when the
    cache is stale, a background refresh is scheduled to update it for next time.
    """
    resolved = deps if deps is not None else default_deps()
    try:
        # A nag (and its background network refresh) only makes sense for an
        # interactive human at a terminal; skip for pipes, CI, and the test suite.
        if not display_context.console.is_terminal:
            return
        if resolved.is_disabled(resolved.environ):
            return
        path = resolved.cache_path(resolved.environ)
        state = resolved.load_state(path)
        now = resolved.now()
        if resolved.is_refresh_due(state, now):
            resolved.spawn(lambda: _refresh_cache(resolved, path, now))
        latest = state.latest_version if state is not None else None
        if latest is None or not resolved.is_newer(resolved.current_version, latest):
            return
        _render_nag(display_context, resolved.current_version, latest, resolved.detect_install())
    except Exception:
        return


def update_status(
    *, allow_network: bool = True, deps: UpdateCheckDeps | None = None
) -> UpdateStatus:
    """Return the current update situation for ``ralph --diagnose``.

    Reads the cached result; when ``allow_network`` is True (an interactive
    diagnose) and the cache is stale, it does a single synchronous, time-boxed
    PyPI refresh. Callers pass ``allow_network=False`` for non-interactive
    contexts (pipes, CI, tests) so the status is computed from cache alone.
    """
    resolved = deps if deps is not None else default_deps()
    install = resolved.detect_install()
    if resolved.is_disabled(resolved.environ):
        return UpdateStatus(resolved.current_version, None, False, install, disabled=True)
    path = resolved.cache_path(resolved.environ)
    state = resolved.load_state(path)
    now = resolved.now()
    latest = state.latest_version if state is not None else None
    if allow_network and resolved.is_refresh_due(state, now):
        fetched = resolved.fetch_latest()
        if fetched is not None:
            latest = fetched
            resolved.save_state(path, VersionCheckState(last_checked=now, latest_version=fetched))
    available = latest is not None and resolved.is_newer(resolved.current_version, latest)
    return UpdateStatus(resolved.current_version, latest, available, install, disabled=False)


def _render_nag(
    display_context: DisplayContext, current: str, latest: str, install: InstallInfo
) -> None:
    arrow = "↑" if display_context.glyphs_enabled else "^"
    header = Text()
    header.append(f"{arrow} ", style="theme.status.warning")
    header.append("Ralph Workflow ", style="theme.status.warning")
    header.append(latest, style="theme.banner.version")
    header.append(f" available (you have {current})", style="theme.status.warning")

    hint = Text()
    hint.append("  upgrade: ", style="theme.text.muted")
    hint.append(install.upgrade_command)

    display_context.console.print(header)
    display_context.console.print(hint)
