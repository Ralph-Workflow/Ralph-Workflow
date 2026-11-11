from loguru import logger

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
        agent_availability(
            agent="claude", available=False, cooldown_ms_remaining=5000, spent=False
        ),
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
        agent_availability(
            agent="claude", available=False, cooldown_ms_remaining=2000, spent=False
        ),
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
        agent_availability(
            agent="claude", available=False, cooldown_ms_remaining=1500, spent=False
        ),
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
                "claude": UnavailabilityEntry(
                    unavailable_until_ms=5000,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=0,
                    base_backoff_ms=5000,
                    max_backoff_ms=5000,
                ),
                "opencode": UnavailabilityEntry(
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


def test_controller_selection_evidence_includes_stored_cooldown_reason() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_entries={
                "cursor/auto": UnavailabilityEntry(
                    unavailable_until_ms=5_000,
                    reason=UnavailabilityReason.AUTH_CONFIG,
                    attempt=0,
                    base_backoff_ms=5_000,
                    max_backoff_ms=60_000,
                )
            },
        )
    )

    selection = controller.preferred_agent_index("development", ["cursor/auto", "fallback"])

    assert selection.agent == "fallback"
    assert selection.skipped_reasons == (
        ("cursor/auto", "cooldown (5000ms remaining, reason=auth_config)"),
    )
    assert format_selection_evidence("development", selection) == (
        "Phase development: Selected agent fallback "
        "(skipped cursor/auto: cooldown (5000ms remaining, reason=auth_config))"
    )


def test_unavailable_agent_with_zero_cooldown_remainder_reports_unavailable_not_zero_ms() -> None:
    rows = [
        agent_availability(agent="claude", available=False, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index == 1
    assert selection.agent == "opencode"
    assert selection.skipped_reasons == (("claude", "unavailable"),)
    assert "0ms remaining" not in selection.skipped_reasons[0][1]


def test_select_preferred_agent_is_a_pure_index_zero_scan() -> None:
    rows = [
        agent_availability(agent="claude", available=True, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="agy", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    for _ in range(3):
        selection = select_preferred_agent(rows)
        assert selection.index == 0
        assert selection.agent == "claude"


def test_select_preferred_agent_skips_unavailable_cooldown_spent_in_priority_order() -> None:
    rows = [
        agent_availability(
            agent="claude",
            available=False,
            cooldown_ms_remaining=3000,
            spent=False,
            cooldown_reason="no_output_at_start",
        ),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=True),
        agent_availability(agent="agy", available=False, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="codex", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index == 3
    assert selection.agent == "codex"
    assert selection.skipped_reasons == (
        ("claude", "cooldown (3000ms remaining, reason=no_output_at_start)"),
        ("opencode", "spent"),
        ("agy", "unavailable"),
    )


def test_select_preferred_agent_returns_none_when_no_agent_selectable() -> None:
    rows = [
        agent_availability(
            agent="claude", available=False, cooldown_ms_remaining=2000, spent=False
        ),
        agent_availability(
            agent="opencode", available=False, cooldown_ms_remaining=5000, spent=False
        ),
        agent_availability(agent="agy", available=True, cooldown_ms_remaining=0, spent=True),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index is None
    assert selection.agent is None
    assert selection.skipped_reasons == (
        ("claude", "cooldown (2000ms remaining)"),
        ("opencode", "cooldown (5000ms remaining)"),
        ("agy", "spent"),
    )


def test_select_preferred_agent_index_zero_with_full_remaining_cooldown() -> None:
    rows = [
        agent_availability(
            agent="claude", available=True, cooldown_ms_remaining=10_000, spent=False
        ),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="agy", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index == 1
    assert selection.agent == "opencode"
    assert selection.skipped_reasons[0] == ("claude", "cooldown (10000ms remaining)")


def test_select_preferred_agent_repeated_calls_with_same_rows_are_stable() -> None:
    rows = [
        agent_availability(agent="claude", available=True, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="opencode", available=True, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="agy", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    selections = [select_preferred_agent(rows) for _ in range(5)]
    for selection in selections:
        assert selection.index == 0
        assert selection.agent == "claude"
        assert selection.skipped_reasons == (
            ("opencode", "lower_priority"),
            ("agy", "lower_priority"),
        )


def test_select_preferred_agent_zero_cooldown_unavailable_is_unavailable() -> None:
    rows = [
        agent_availability(agent="claude", available=False, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="opencode", available=False, cooldown_ms_remaining=0, spent=False),
        agent_availability(agent="agy", available=True, cooldown_ms_remaining=0, spent=False),
    ]
    selection = select_preferred_agent(rows)
    assert selection.index == 2
    assert selection.agent == "agy"
    assert selection.skipped_reasons == (
        ("claude", "unavailable"),
        ("opencode", "unavailable"),
    )


def test_preferred_agent_index_deduplicates_unchanged_all_unavailable_logs() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_entries={
                "claude": UnavailabilityEntry(
                    unavailable_until_ms=5_000,
                    reason=UnavailabilityReason.AUTH_CONFIG,
                    attempt=0,
                    base_backoff_ms=5_000,
                    max_backoff_ms=60_000,
                ),
                "opencode": UnavailabilityEntry(
                    unavailable_until_ms=8_000,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=0,
                    base_backoff_ms=5_000,
                    max_backoff_ms=60_000,
                ),
            },
        )
    )

    logs: list[str] = []
    sink_id = logger.add(logs.append, level="INFO", format="{message}")
    try:
        sel1 = controller.preferred_agent_index("development", ["claude", "opencode"])
        assert sel1.agent is None
        assert len(logs) == 1
        assert "Phase development: No selectable agent" in logs[0]
        assert "claude" in logs[0]
        assert "opencode" in logs[0]

        sel2 = controller.preferred_agent_index("development", ["claude", "opencode"])
        assert sel2.agent is None
        assert len(logs) == 1

        clock.advance(2.0)
        sel3 = controller.preferred_agent_index("development", ["claude", "opencode"])
        assert sel3.agent is None
        assert len(logs) == 1

        clock.advance(5.0)
        sel4 = controller.preferred_agent_index("development", ["claude", "opencode"])
        assert sel4.agent == "claude"
        assert len(logs) == 2
        assert "Phase development: Selected agent claude" in logs[1]

        sel5 = controller.preferred_agent_index("development", ["claude", "opencode"])
        assert sel5.agent == "claude"
        assert len(logs) == 2
    finally:
        logger.remove(sink_id)


def test_preferred_agent_index_deduplicates_unchanged_spent_chain() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))
    controller.note_retry_exhaustion("development", "agent_a")
    controller.note_retry_exhaustion("development", "agent_b")

    logs: list[str] = []
    sink_id = logger.add(logs.append, level="INFO", format="{message}")
    try:
        sel1 = controller.preferred_agent_index("development", ["agent_a", "agent_b"])
        assert sel1.agent is None
        assert len(logs) == 1
        assert "Phase development: No selectable agent" in logs[0]

        for _ in range(5):
            sel = controller.preferred_agent_index("development", ["agent_a", "agent_b"])
            assert sel.agent is None
        assert len(logs) == 1
    finally:
        logger.remove(sink_id)
