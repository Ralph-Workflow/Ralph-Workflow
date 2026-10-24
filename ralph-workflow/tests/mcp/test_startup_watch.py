"""Edge cases of the BOOT -> ENVELOPE startup watch, under an injected clock."""

from __future__ import annotations

import pytest

from ralph.mcp.server._startup_watch import StartupVerdict, StartupWatch

ENVELOPE_S = 10.0
BOOT_S = 60.0


class _Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _Stage:
    """Stage file stand-in: ``None`` until the child records its first stage."""

    def __init__(self) -> None:
        self.value: str | None = None
        self.reads = 0

    def __call__(self) -> str | None:
        self.reads += 1
        return self.value


def _watch(
    clock: _Clock,
    stage: _Stage,
    *,
    boot_allowance_s: float = BOOT_S,
    poll_interval_s: float = 0.25,
) -> StartupWatch:
    return StartupWatch(
        spawn_start=clock.now,
        envelope_s=ENVELOPE_S,
        boot_allowance_s=boot_allowance_s,
        read_stage=stage,
        clock=clock,
        stage_poll_interval_s=poll_interval_s,
    )


def test_slow_boot_does_not_consume_the_envelope() -> None:
    """A boot longer than the envelope is fine while the allowance holds."""
    clock, stage = _Clock(), _Stage()
    watch = _watch(clock, stage)

    clock.now += ENVELOPE_S + 5  # would have tripped a spawn-anchored deadline
    assert watch.check() is StartupVerdict.WAITING

    stage.value = "env_loaded"
    clock.now += 1
    assert watch.check() is StartupVerdict.WAITING
    assert watch.child_started_at == clock.now


def test_envelope_is_measured_from_the_childs_first_stage() -> None:
    clock, stage = _Clock(), _Stage()
    watch = _watch(clock, stage)
    clock.now += 40
    stage.value = "env_loaded"
    assert watch.check() is StartupVerdict.WAITING

    clock.now += ENVELOPE_S - 0.01
    assert watch.check() is StartupVerdict.WAITING
    clock.now += 0.02
    assert watch.check() is StartupVerdict.ENVELOPE_DEADLINE_EXCEEDED


def test_child_that_never_boots_trips_the_boot_deadline() -> None:
    clock, stage = _Clock(), _Stage()
    watch = _watch(clock, stage)

    clock.now += BOOT_S - 0.01
    assert watch.check() is StartupVerdict.WAITING
    clock.now += 0.02
    assert watch.check() is StartupVerdict.BOOT_DEADLINE_EXCEEDED


def test_boot_deadline_boundary_is_inclusive() -> None:
    clock, stage = _Clock(), _Stage()
    watch = _watch(clock, stage)
    clock.now += BOOT_S
    assert watch.check() is StartupVerdict.BOOT_DEADLINE_EXCEEDED


def test_first_stage_landing_exactly_at_the_boot_boundary_wins() -> None:
    """A child that began on the last tick is judged by the envelope, not boot."""
    clock, stage = _Clock(), _Stage()
    watch = _watch(clock, stage)
    clock.now += BOOT_S
    stage.value = "env_loaded"
    assert watch.check() is StartupVerdict.WAITING


def test_without_a_boot_allowance_the_envelope_is_spawn_anchored() -> None:
    clock, stage = _Clock(), _Stage()
    watch = _watch(clock, stage, boot_allowance_s=0.0)
    assert not watch.split_phases

    clock.now += ENVELOPE_S - 0.01
    assert watch.check() is StartupVerdict.WAITING
    clock.now += 0.02
    assert watch.check() is StartupVerdict.ENVELOPE_DEADLINE_EXCEEDED
    assert stage.reads == 0


def test_negative_boot_allowance_is_treated_as_disabled() -> None:
    clock, stage = _Clock(), _Stage()
    watch = _watch(clock, stage, boot_allowance_s=-5.0)
    assert not watch.split_phases
    assert watch.total_budget_s == ENVELOPE_S


def test_stage_file_is_polled_at_the_configured_cadence_only() -> None:
    clock, stage = _Clock(), _Stage()
    watch = _watch(clock, stage, poll_interval_s=0.25)

    for _ in range(10):  # ten ticks inside one poll interval
        clock.now += 0.02
        watch.check()
    assert stage.reads == 1

    clock.now += 0.25
    watch.check()
    assert stage.reads == 2


def test_stage_file_stops_being_read_once_the_child_has_started() -> None:
    clock, stage = _Clock(), _Stage()
    stage.value = "env_loaded"
    watch = _watch(clock, stage)
    watch.check()
    reads_after_start = stage.reads
    for _ in range(5):
        clock.now += 1
        watch.check()
    assert stage.reads == reads_after_start


def test_total_budget_is_envelope_plus_boot_allowance() -> None:
    clock, stage = _Clock(), _Stage()
    assert _watch(clock, stage).total_budget_s == pytest.approx(ENVELOPE_S + BOOT_S)
