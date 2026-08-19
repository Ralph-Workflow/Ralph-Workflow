"""Unit tests for the pure priority agent selection module."""

from ralph.recovery.agent_selection import (
    agent_availability,
    format_selection_evidence,
    select_preferred_agent,
)


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
    evidence = format_selection_evidence(selection)
    assert evidence == "Selected agent agy (skipped claude: cooldown (1500ms remaining); opencode: spent)"
