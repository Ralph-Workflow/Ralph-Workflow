"""``_await_mcp_startup_or_failure`` budgets child boot apart from the pre-serve envelope.

Regression: the parent anchored one ~29.8s deadline at spawn, but the child's
own startup clock only starts after interpreter boot and imports. Under heavy
host pressure a healthy, still-booting child was killed with
``startup_deadline_exceeded`` and no log output at all.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol._startup_timeouts import mcp_boot_allowance_from_env
from ralph.mcp.protocol.env import MCP_BOOT_ALLOWANCE_MS_ENV
from ralph.mcp.server import lifecycle
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server._start_stage_tracker import stage_file_path

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

STAGE_ID = "boot-allowance-spawn"
ENVELOPE_S = 0.5  # preflight_timeout 1s -> envelope 0.5s


class _Process:
    """Minimal ``ProcessLike`` stand-in: a child that never exits on its own.

    ``pid`` is not a real pid, so the psutil lookup behind the startup
    diagnostics stays best-effort.
    """

    def __init__(self) -> None:
        self.terminations = 0

    @property
    def pid(self) -> int:
        return 0

    def poll(self) -> int | None:
        return None

    def terminate(self, grace_period_s: float = 5.0) -> None:
        del grace_period_s
        self.terminations += 1

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return 0

    def kill(self) -> None:
        """The teardown path terminates the child; it never kills it directly."""


def _bridge(root: Path, process: _Process) -> StandaloneMcpProcess:
    """A real ``StandaloneMcpProcess`` around the fake child.

    The production wait loop only ever touches the bridge's child process and
    its ``shutdown()``, so the test drives the real dataclass instead of a
    hand-rolled stand-in.
    """
    session_file = root / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint="http://127.0.0.1:1/mcp",
        process=process,
        session_file=session_file,
    )


def _spawn_process(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    *,
    phase: str | None = None,
    pass_fds: tuple[int, ...] = (),
) -> _Process:
    """Spawn the fake child the wait loop only ever polls."""
    del command, cwd, env, phase, pass_fds
    return _Process()


def _deps(
    *,
    boot_allowance_s: float | None,
    preflight_release: threading.Event,
    seen_budgets: list[timedelta],
) -> lifecycle.LifecycleDeps:
    def preflight(endpoint: str, tools: list[str], timeout: timedelta) -> None:
        del endpoint, tools
        seen_budgets.append(timeout)
        preflight_release.wait(timeout=5)

    return lifecycle.LifecycleDeps(
        create_session_file=lambda root, session: root,
        subprocess_env=lambda session_file: {},
        spawn_process=_spawn_process,
        preflight=preflight,
        preflight_timeout=lambda: timedelta(seconds=1),
        boot_allowance=(
            None if boot_allowance_s is None else lambda: timedelta(seconds=boot_allowance_s)
        ),
    )


class _TickClock:
    """Monotonic clock that advances ``step`` per read and fires time-keyed hooks."""

    def __init__(self, step: float = 1.0) -> None:
        self.now = 0.0
        self.step = step
        self.hooks: list[tuple[float, Callable[[], None]]] = []

    def at(self, when: float, hook: Callable[[], None]) -> None:
        self.hooks.append((when, hook))

    def __call__(self) -> float:
        self.now += self.step
        for when, hook in list(self.hooks):
            if self.now >= when:
                self.hooks.remove((when, hook))
                hook()
        return self.now


def _await(
    tmp_path: Path,
    deps: lifecycle.LifecycleDeps,
    bridge: StandaloneMcpProcess,
    clock: _TickClock | None = None,
) -> lifecycle.McpServerError | None:
    tick = clock or _TickClock()
    try:
        lifecycle._await_mcp_startup_or_failure(
            bridge,
            deps=deps,
            endpoint="http://127.0.0.1:1/mcp",
            visible_tools=[],
            port=1,
            env={},
            spawn_start=0.0,
            root=tmp_path,
            log_offset=0,
            stage_id=STAGE_ID,
            clock=tick,
        )
    except lifecycle.McpServerError as exc:
        return exc
    return None


def _write_stage(tmp_path: Path, value: str = "env_loaded") -> None:
    path = stage_file_path(tmp_path, spawn_id=STAGE_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="ascii")


def test_child_that_never_boots_fails_with_a_boot_specific_error(tmp_path: Path) -> None:
    release = threading.Event()
    budgets: list[timedelta] = []
    process = _Process()
    bridge = _bridge(tmp_path, process)
    try:
        err = _await(
            tmp_path,
            _deps(boot_allowance_s=3.0, preflight_release=release, seen_budgets=budgets),
            bridge,
        )
    finally:
        release.set()

    assert err is not None
    assert err.restart_reason == "boot_deadline_exceeded"
    assert "boot allowance" in str(err)
    assert err.last_stage == "last_stage_unknown"
    assert process.terminations == 1


def test_booted_child_is_judged_by_the_envelope_from_its_first_stage(tmp_path: Path) -> None:
    release = threading.Event()
    budgets: list[timedelta] = []
    _write_stage(tmp_path)
    try:
        err = _await(
            tmp_path,
            _deps(boot_allowance_s=30.0, preflight_release=release, seen_budgets=budgets),
            _bridge(tmp_path, _Process()),
        )
    finally:
        release.set()

    assert err is not None
    assert err.restart_reason == "startup_deadline_exceeded"
    assert err.last_stage == "env_loaded"


def test_slow_boot_longer_than_the_envelope_still_becomes_ready(tmp_path: Path) -> None:
    """Boot (3s) outlasts the 0.5s envelope yet readiness inside the allowance succeeds."""
    release = threading.Event()
    budgets: list[timedelta] = []
    clock = _TickClock(step=0.2)
    clock.at(3.0, lambda: _write_stage(tmp_path))
    clock.at(3.4, release.set)

    err = _await(
        tmp_path,
        _deps(boot_allowance_s=10.0, preflight_release=release, seen_budgets=budgets),
        _bridge(tmp_path, _Process()),
        clock,
    )

    assert err is None


def test_preflight_budget_is_extended_by_the_boot_allowance(tmp_path: Path) -> None:
    release = threading.Event()
    budgets: list[timedelta] = []
    release.set()
    _await(
        tmp_path,
        _deps(boot_allowance_s=7.0, preflight_release=release, seen_budgets=budgets),
        _bridge(tmp_path, _Process()),
    )
    assert budgets == [timedelta(seconds=8)]


def test_no_boot_allowance_keeps_the_spawn_anchored_deadline(tmp_path: Path) -> None:
    release = threading.Event()
    budgets: list[timedelta] = []
    try:
        err = _await(
            tmp_path,
            _deps(boot_allowance_s=None, preflight_release=release, seen_budgets=budgets),
            _bridge(tmp_path, _Process()),
        )
    finally:
        release.set()

    assert err is not None
    assert err.restart_reason == "startup_deadline_exceeded"
    assert budgets == [timedelta(seconds=1)]


def test_failing_boot_allowance_provider_degrades_to_no_allowance(tmp_path: Path) -> None:
    def boom() -> timedelta:
        raise RuntimeError("provider failed")

    deps = lifecycle.LifecycleDeps(
        create_session_file=lambda root, session: root,
        subprocess_env=lambda session_file: {},
        spawn_process=_spawn_process,
        preflight=lambda *a: None,
        preflight_timeout=lambda: timedelta(seconds=1),
        boot_allowance=boom,
    )
    assert lifecycle._boot_allowance_seconds(deps) == 0.0


def test_boot_allowance_default_and_env_override() -> None:
    assert mcp_boot_allowance_from_env({}) == timedelta(seconds=90)
    assert mcp_boot_allowance_from_env({MCP_BOOT_ALLOWANCE_MS_ENV: "1500"}) == timedelta(
        milliseconds=1500
    )
    # Unparseable values fall back to the default instead of disabling the guard.
    assert mcp_boot_allowance_from_env({MCP_BOOT_ALLOWANCE_MS_ENV: "soon"}) == timedelta(seconds=90)


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_boot_allowance_env_is_clamped_to_at_least_one_millisecond(raw: str) -> None:
    assert mcp_boot_allowance_from_env({MCP_BOOT_ALLOWANCE_MS_ENV: raw}) == timedelta(
        milliseconds=1
    )
