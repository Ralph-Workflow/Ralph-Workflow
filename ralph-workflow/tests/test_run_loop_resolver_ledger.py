"""The run loop hands its agent cooldown ledger to every integration resolver path."""

from __future__ import annotations

import dataclasses
from io import StringIO

from rich.console import Console

from ralph.agents.timeout_clock import FakeClock
from ralph.display.context import make_display_context
from ralph.pipeline import run_loop
from ralph.recovery.agent_unavailability_tracker import AgentUnavailabilityTracker
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._resolver_controller import make_resolver_controller


def _deps() -> run_loop.PipelineDeps:
    return make_test_pipeline_deps(make_display_context(console=Console(file=StringIO())))


def test_run_ledger_is_the_controllers_unavailability_store() -> None:
    controller = make_resolver_controller()

    seeded = run_loop._with_run_ledger(_deps(), controller)

    assert seeded is not None
    assert seeded.unavailability_store is controller.unavailability_store


def test_run_without_pipeline_deps_has_nothing_to_seed() -> None:
    assert run_loop._with_run_ledger(None, make_resolver_controller()) is None


def test_the_controller_stays_the_only_ledger_even_when_deps_carry_another() -> None:
    other = AgentUnavailabilityTracker(clock=FakeClock())
    deps = dataclasses.replace(_deps(), unavailability_store=other)
    controller = make_resolver_controller()

    seeded = run_loop._with_run_ledger(deps, controller)

    assert seeded is not None
    assert seeded.unavailability_store is controller.unavailability_store
