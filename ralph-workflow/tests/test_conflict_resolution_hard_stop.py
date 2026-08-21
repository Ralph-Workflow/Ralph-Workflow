"""Tests for the resolution driver's OWN wall-clock stop.

The driver used to bound a round by handing the agent layer a session
ceiling and then blocking on the call. That is a bound only for as long
as the agent layer honours it: every watchdog in
``ralph.agents.invoke`` runs INSIDE the very call the driver is waiting
on, so a wedge anywhere below the driver -- a reader that never pumps, a
watchdog that never evaluates, a process that cannot be signalled --
leaves the driver blocked for the rest of the run with a rebase paused
mid-replay and no way out but SIGKILL.

A ceiling the driver cannot enforce is not a ceiling. These tests pin
the enforcement the driver owns itself: an attempt that does not come
back within its share is ABANDONED, its agent processes are reaped, and
the round is failed so the caller can abort the rebase.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import (
    RESOLVE_TIMEOUT_SECONDS,
    run_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.graph import MAX_RESOLUTION_ROUNDS
from ralph.pipeline.conflict_resolution.hard_stop import (
    call_with_hard_stop,
    live_agent_pids,
    reap_agents_started_since,
)
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["src/alpha.py"]


@lru_cache(maxsize=1)
def _policy_bundle() -> PolicyBundle:
    """The real default policy, which declares the resolution drain."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


class _FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class _RecordingHardStop:
    """Stands in for the driver's hard stop; every attempt is abandoned.

    An abandoned attempt costs its whole share -- the driver waited out
    the timeout before giving up -- so the clock is advanced by it. A
    fake that abandons for free would let a share of the WHOLE remaining
    deadline look bounded, because nothing would ever be spent.
    """

    def __init__(self, clock: _FakeClock) -> None:
        self.clock = clock
        self.timeouts: list[float] = []

    def __call__(self, call: object, timeout_seconds: float) -> bool | None:
        del call
        self.timeouts.append(timeout_seconds)
        self.clock.now += timeout_seconds
        return None


class _FakeRecord:
    """The fields :func:`reap_agents_started_since` reads off a record."""

    def __init__(self, pid: int, label: str | None) -> None:
        self.pid = pid
        self.pgid = pid
        self.label = label


class _FakeProcessManager:
    """A process manager whose live set the test controls."""

    def __init__(self, records: list[_FakeRecord]) -> None:
        self.records = records

    def list_active(self) -> list[_FakeRecord]:
        return list(self.records)


class _ExplodingProcessManager:
    """A process manager that fails the way a racing one does."""

    def list_active(self) -> list[_FakeRecord]:
        msg = "dictionary changed size during iteration"
        raise RuntimeError(msg)


def _install_seams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Stub the git queries and the prompt render; leave the budget real."""
    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(_CONFLICTED))
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda root, paths: list(_CONFLICTED),
    )
    prompt_path = tmp_path / "conflict-prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    monkeypatch.setattr(driver_module, "render_conflict_prompt", lambda **kwargs: prompt_path)


def test_an_attempt_that_never_returns_fails_its_round(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The driver reports failure instead of blocking on a wedged agent."""
    _install_seams(monkeypatch, tmp_path)
    clock = _FakeClock()
    hard_stop = _RecordingHardStop(clock)

    resolved = run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        clock=clock,
        hard_stop=hard_stop,
    )

    assert resolved is False
    assert hard_stop.timeouts, "the driver never applied a hard stop"


def test_every_hard_stop_is_a_bounded_share_of_the_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No attempt may be given an unbounded, zero, or whole-ceiling stop."""
    _install_seams(monkeypatch, tmp_path)
    clock = _FakeClock()
    hard_stop = _RecordingHardStop(clock)

    run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        clock=clock,
        hard_stop=hard_stop,
    )

    assert hard_stop.timeouts, "the driver never applied a hard stop"
    for timeout in hard_stop.timeouts:
        assert timeout > 0.0
    # The share must leave room for the rounds the driver still promises
    # to run. Handing the first attempt everything that is left satisfies
    # "no attempt exceeded the ceiling" while starving every retry, which
    # is the arithmetic this bound exists to forbid.
    assert hard_stop.timeouts[0] <= RESOLVE_TIMEOUT_SECONDS / MAX_RESOLUTION_ROUNDS
    assert sum(hard_stop.timeouts) <= RESOLVE_TIMEOUT_SECONDS
    assert clock.now - 1_000.0 <= RESOLVE_TIMEOUT_SECONDS


def test_a_call_that_returns_in_time_yields_its_value() -> None:
    """The hard stop is transparent to an attempt that finishes."""
    assert call_with_hard_stop(lambda: True, 5.0) is True
    assert call_with_hard_stop(lambda: False, 5.0) is False


def test_a_call_that_outlives_its_stop_is_abandoned() -> None:
    """A blocked attempt returns control to the driver instead of hanging."""
    release = threading.Event()

    def _never_returns_in_time() -> bool:
        return release.wait(timeout=30.0)

    try:
        assert call_with_hard_stop(_never_returns_in_time, 0.05) is None
    finally:
        release.set()


def test_a_raising_call_is_reported_as_a_failed_attempt() -> None:
    """An attempt that dies is a failed round, never a hang or a crash."""

    def _raises() -> bool:
        msg = "agent layer exploded"
        raise RuntimeError(msg)

    assert call_with_hard_stop(_raises, 5.0) is False


def test_only_processes_the_attempt_started_are_reaped() -> None:
    """A reap must not touch what was already running."""
    already_running = _FakeRecord(101, "invoke:claude")
    started_by_the_attempt = _FakeRecord(202, "invoke:claude")
    manager = _FakeProcessManager([already_running, started_by_the_attempt])
    killed: list[int] = []

    reaped = reap_agents_started_since(
        frozenset({already_running.pid}),
        manager=manager,
        teardown=killed.append,
    )

    assert reaped == (started_by_the_attempt.pid,)
    assert killed == [started_by_the_attempt.pid]


def test_the_reap_covers_the_session_mcp_server_not_just_the_agent() -> None:
    """The agent is not the only thing an abandoned attempt leaves running.

    The MCP server the session runs on is what spawns the tool
    subprocesses that rewrite files -- a formatter, a codemod, a test
    run. Those live in the SERVER's process registry, not this one, so
    reaping them means reaping the server, whose subtree they are in.
    Reaping the agent alone leaves them running into the caller's
    ``git rebase --abort``.
    """
    manager = _FakeProcessManager(
        [
            _FakeRecord(11, "invoke:claude"),
            _FakeRecord(13, "phase:rebase_conflict_resolution:mcp-server"),
            _FakeRecord(14, "some-unrelated-thing"),
            _FakeRecord(15, None),
        ]
    )
    killed: list[int] = []

    reaped = reap_agents_started_since(frozenset(), manager=manager, teardown=killed.append)

    assert set(reaped) == {11, 13}
    assert 14 not in killed
    assert 15 not in killed


def test_a_failing_reap_never_escapes_the_abandon_path() -> None:
    """The abandonment must survive a process manager that raises.

    ``list_active`` walks a dict other threads mutate, so it can raise
    ``RuntimeError`` on its own. Letting that escape would lose the
    abandonment entirely: no verdict, no phase line, and the driver back
    to waiting on a wedged layer.
    """
    manager = _ExplodingProcessManager()

    assert reap_agents_started_since(frozenset(), manager=manager, teardown=lambda pid: None) == ()
    assert live_agent_pids(manager=manager) is None


def test_a_reaped_process_that_will_not_die_does_not_block_the_others() -> None:
    """One unkillable process may not strand its siblings."""
    manager = _FakeProcessManager(
        [_FakeRecord(21, "invoke:claude"), _FakeRecord(22, "invoke:codex")]
    )
    killed: list[int] = []

    def _teardown(pid: int) -> None:
        if pid == 21:
            msg = "no such process"
            raise OSError(msg)
        killed.append(pid)

    reaped = reap_agents_started_since(frozenset(), manager=manager, teardown=_teardown)

    assert killed == [22]
    assert reaped == (22,)


def test_an_abandoned_attempt_ends_the_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One wedge is enough; the driver does not go back for another.

    An abandoned attempt leaves a thread the interpreter cannot reclaim
    and a session whose Python-side objects are stranded on it. Starting
    the next candidate against a layer that has just proven it does not
    return spends another share of the deadline to strand another one.
    """
    _install_seams(monkeypatch, tmp_path)
    clock = _FakeClock()
    hard_stop = _RecordingHardStop(clock)

    resolved = run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=UnifiedConfig.model_validate({"general": {}}),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        clock=clock,
        hard_stop=hard_stop,
    )

    assert resolved is False
    assert len(hard_stop.timeouts) == 1


def test_an_unreadable_snapshot_disables_the_reap() -> None:
    """Not knowing what was already running must not mean killing it.

    The snapshot and the reap read the same registry, and that registry
    raises under exactly the race the reap exists to survive. Treating a
    failed snapshot as "nothing was running" makes every live agent and
    every live MCP server in the process -- the parent run's included --
    look like something this attempt started.
    """
    manager = _FakeProcessManager([_FakeRecord(31, "invoke:claude")])
    killed: list[int] = []

    reaped = reap_agents_started_since(None, manager=manager, teardown=killed.append)

    assert reaped == ()
    assert killed == []


def test_abandoning_an_attempt_actually_reaps_what_it_started() -> None:
    """The stop's second promise, wired end to end.

    Bounding the wait is half of it; the other half is that nothing the
    abandoned attempt started is still able to write to the repository
    the caller is about to `git rebase --abort`.
    """
    release = threading.Event()
    manager = _FakeProcessManager([])
    killed: list[int] = []

    def _never_returns_in_time() -> bool:
        # The agent this attempt starts, appearing only after the stop's
        # own snapshot was taken -- which is what marks it as this
        # attempt's to reap.
        manager.records.append(_FakeRecord(41, "invoke:claude"))
        return release.wait(timeout=20.0)

    try:
        outcome = call_with_hard_stop(
            _never_returns_in_time,
            0.05,
            manager=manager,
            teardown=killed.append,
        )
    finally:
        release.set()

    assert outcome is None
    assert killed == [41]


def test_reporting_an_abandonment_cannot_block_the_abandonment() -> None:
    """Saying so must not cost what saying it was supposed to save.

    Ralph's log sink prints through the SAME rich Console the status bar
    paints with, so a worker wedged inside a display write holds the
    lock the abandonment's own diagnostics need. Reporting on the
    caller's thread hands the freeze straight back.
    """
    release = threading.Event()
    reporting = threading.Event()

    def _blocking_report(timeout_seconds: float, reaped: tuple[int, ...]) -> None:
        del timeout_seconds, reaped
        reporting.set()
        release.wait(timeout=20.0)

    def _never_returns_in_time() -> bool:
        return release.wait(timeout=20.0)

    try:
        outcome = call_with_hard_stop(
            _never_returns_in_time,
            0.05,
            manager=_FakeProcessManager([]),
            teardown=lambda pid: None,
            report=_blocking_report,
        )
    finally:
        release.set()

    assert outcome is None
