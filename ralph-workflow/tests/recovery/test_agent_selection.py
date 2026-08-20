"""Unit tests for priority agent selection and its controller surface."""

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_selection import (
    agent_availability,
    format_selection_evidence,
    select_preferred_agent,
)
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason


def test_lowest_index_wins_among_selectable_agents() -> None:
    rows = [
        agent_availability(agent="claude", available=True, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="agy", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index == 0
    assert selection.agent == "claude"
    assert selection.skipped_reasons == (
        ("opencode", "lower_priority"),
        ("agy", "lower_priority"),
    )


def test_agent_in_cooldown_never_picked_even_if_index_zero() -> None:
    rows = [
        agent_availability(agent="claude", available=False, cooldown_ms_remaining=5000, spent=False),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="agy", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index == 1
    assert selection.agent == "opencode"
    assert selection.skipped_reasons == (
        ("claude", "cooldown (5000ms remaining)"),
        ("agy", "lower_priority"),
    )


def test_allowance_spent_agent_never_picked() -> None:
    rows = [
        agent_availability(agent="claude", available=True, cooldown_ms_remaining=0, spent=True),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="agy", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index == 1
    assert selection.agent == "opencode"
    assert selection.skipped_reasons == (
        ("claude", "spent"),
        ("agy", "lower_priority"),
    )


def test_current_agent_returned_when_highest_priority_selectable() -> None:
    rows = [
        agent_availability(agent="claude", available=True, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index == 0
    assert selection.agent == "claude"


def test_returns_none_when_nothing_selectable() -> None:
    rows = [
        agent_availability(agent="claude", available=False, cooldown_ms_remaining=2000, spent=False),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=True),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index is None
    assert selection.agent is None
    assert selection.skipped_reasons == (
        ("claude", "cooldown (2000ms remaining)"),
        ("opencode", "spent"),
    )


def test_skipped_reasons_formatting_and_evidence() -> None:
    rows = [
        agent_availability(agent="claude", available=False, cooldown_ms_remaining=1500, spent=False),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=True),
        agent_availability(agent="agy", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index == 2
    assert selection.agent == "agy"
    assert selection.skipped_reasons == (
        ("claude", "cooldown (1500ms remaining)"),
        ("opencode", "spent"),
    )
    evidence = format_selection_evidence("development", selection)
    assert evidence == (
        "Phase development: Selected agent agy "
        "(skipped claude: cooldown (1500ms remaining); opencode: spent)"
    )


def test_controller_earliest_available_wait_uses_smallest_remaining_cooldown() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_entries={
                "development:claude": UnavailabilityEntry(
                    unavailable_until_ms=5000,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=0,
                    base_backoff_ms=5000,
                    max_backoff_ms=5000,
                ),
                "development:opencode": UnavailabilityEntry(
                    unavailable_until_ms=8000,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=0,
                    base_backoff_ms=5000,
                    max_backoff_ms=5000,
                ),
            },
        )
    )

    assert controller.earliest_available_wait_ms("development", ["claude", "opencode"]) == 5000
    clock.advance(5.0)
    assert controller.earliest_available_wait_ms("development", ["claude", "opencode"]) == 0
