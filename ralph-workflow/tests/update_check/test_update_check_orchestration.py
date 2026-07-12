"""End-to-end orchestration of the nag and diagnose surfaces (no threads/network)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ralph.update_check import (
    UpdateCheckDeps,
    maybe_render_update_nag,
    update_status,
)
from ralph.update_check.compare import is_newer
from ralph.update_check.environment import InstallInfo, InstallKind
from ralph.update_check.state import VersionCheckState, is_refresh_due


class _FakeConsole:
    def __init__(self, *, is_terminal: bool = True) -> None:
        self.printed: list[object] = []
        self.is_terminal = is_terminal

    def print(self, renderable: object) -> None:
        self.printed.append(renderable)


class _FakeDisplayContext:
    def __init__(self, *, is_terminal: bool = True) -> None:
        self.console = _FakeConsole(is_terminal=is_terminal)
        self.glyphs_enabled = True


_INSTALL = InstallInfo(InstallKind.PIPX, "pipx upgrade ralph-workflow")


def _deps(
    *,
    state: VersionCheckState | None,
    now: float = 1000.0,
    disabled: bool = False,
    fetch_latest: Callable[[], str | None] | None = None,
    spawn_calls: list[Callable[[], None]] | None = None,
    saved: list[tuple[Path, VersionCheckState]] | None = None,
    current: str = "0.8.24",
) -> UpdateCheckDeps:
    def _spawn(target: Callable[[], None]) -> None:
        if spawn_calls is not None:
            spawn_calls.append(target)

    def _save(path: Path, value: VersionCheckState) -> None:
        if saved is not None:
            saved.append((path, value))

    return UpdateCheckDeps(
        current_version=current,
        environ={},
        now=lambda: now,
        is_disabled=lambda _env: disabled,
        cache_path=lambda _env: Path("/cache/version-check.json"),
        load_state=lambda _path: state,
        save_state=_save,
        is_refresh_due=is_refresh_due,
        is_newer=is_newer,
        detect_install=lambda: _INSTALL,
        fetch_latest=fetch_latest if fetch_latest is not None else (lambda: None),
        spawn=_spawn,
    )


def test_nag_suppressed_when_disabled() -> None:
    ctx = _FakeDisplayContext()
    spawn_calls: list[Callable[[], None]] = []
    deps = _deps(state=None, disabled=True, spawn_calls=spawn_calls)
    maybe_render_update_nag(ctx, deps=deps)
    assert ctx.console.printed == []
    assert spawn_calls == []


def test_nag_skipped_on_non_interactive_terminal() -> None:
    ctx = _FakeDisplayContext(is_terminal=False)
    spawn_calls: list[Callable[[], None]] = []

    def _boom() -> str | None:
        raise AssertionError("must not fetch when non-interactive")

    fresh = VersionCheckState(last_checked=1000.0, latest_version="0.9.1")
    deps = _deps(state=fresh, now=1000.0, spawn_calls=spawn_calls, fetch_latest=_boom)
    maybe_render_update_nag(ctx, deps=deps)
    # No output and no background refresh spawned when not attached to a terminal.
    assert ctx.console.printed == []
    assert spawn_calls == []


def test_nag_rendered_when_cache_reports_newer() -> None:
    ctx = _FakeDisplayContext()
    fresh = VersionCheckState(last_checked=1000.0, latest_version="0.9.1")
    deps = _deps(state=fresh, now=1000.0)
    maybe_render_update_nag(ctx, deps=deps)
    rendered = " ".join(str(item) for item in ctx.console.printed)
    assert "0.9.1" in rendered
    assert "pipx upgrade ralph-workflow" in rendered


def test_no_nag_when_up_to_date() -> None:
    ctx = _FakeDisplayContext()
    fresh = VersionCheckState(last_checked=1000.0, latest_version="0.8.24")
    deps = _deps(state=fresh, now=1000.0)
    maybe_render_update_nag(ctx, deps=deps)
    assert ctx.console.printed == []


def test_stale_cache_schedules_background_refresh() -> None:
    ctx = _FakeDisplayContext()
    spawn_calls: list[Callable[[], None]] = []
    deps = _deps(state=None, spawn_calls=spawn_calls)
    maybe_render_update_nag(ctx, deps=deps)
    assert len(spawn_calls) == 1


def test_fresh_cache_does_not_refresh() -> None:
    ctx = _FakeDisplayContext()
    spawn_calls: list[Callable[[], None]] = []
    fresh = VersionCheckState(last_checked=1000.0, latest_version="0.9.1")
    deps = _deps(state=fresh, now=1000.0, spawn_calls=spawn_calls)
    maybe_render_update_nag(ctx, deps=deps)
    assert spawn_calls == []


def test_scheduled_refresh_fetches_and_persists() -> None:
    ctx = _FakeDisplayContext()
    spawn_calls: list[Callable[[], None]] = []
    saved: list[tuple[Path, VersionCheckState]] = []
    deps = _deps(
        state=None,
        now=555.0,
        fetch_latest=lambda: "0.9.1",
        spawn_calls=spawn_calls,
        saved=saved,
    )
    maybe_render_update_nag(ctx, deps=deps)
    # Execute the scheduled background job synchronously.
    spawn_calls[0]()
    assert saved == [
        (Path("/cache/version-check.json"), VersionCheckState(last_checked=555.0, latest_version="0.9.1"))
    ]


def test_update_status_disabled_reports_no_fetch() -> None:
    def _boom() -> str | None:
        raise AssertionError("must not fetch when disabled")

    deps = _deps(state=None, disabled=True, fetch_latest=_boom)
    status = update_status(deps=deps)
    assert status.disabled is True
    assert status.update_available is False
    assert status.latest_version is None
    assert status.install is _INSTALL


def test_update_status_refreshes_stale_cache() -> None:
    saved: list[tuple[Path, VersionCheckState]] = []
    deps = _deps(state=None, now=42.0, fetch_latest=lambda: "0.9.1", saved=saved)
    status = update_status(deps=deps)
    assert status.update_available is True
    assert status.latest_version == "0.9.1"
    assert saved[0][1] == VersionCheckState(last_checked=42.0, latest_version="0.9.1")


def test_update_status_uses_fresh_cache_without_fetch() -> None:
    def _boom() -> str | None:
        raise AssertionError("must not fetch when cache is fresh")

    fresh = VersionCheckState(last_checked=1000.0, latest_version="0.9.1")
    deps = _deps(state=fresh, now=1000.0, fetch_latest=_boom)
    status = update_status(deps=deps)
    assert status.update_available is True
    assert status.latest_version == "0.9.1"


def test_update_status_cache_only_when_network_disallowed() -> None:
    def _boom() -> str | None:
        raise AssertionError("must not fetch when network is disallowed")

    # Stale cache, but allow_network=False must not trigger a fetch.
    stale = VersionCheckState(last_checked=0.0, latest_version="0.8.5")
    deps = _deps(state=stale, now=10_000_000.0, fetch_latest=_boom)
    status = update_status(allow_network=False, deps=deps)
    assert status.latest_version == "0.8.5"
    assert status.update_available is False
