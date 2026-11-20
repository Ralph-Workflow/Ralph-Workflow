from __future__ import annotations

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution.resolver_health import (
    choose_resolver,
    record_resolver_failure,
    record_resolver_success,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

CURSOR_USAGE_LIMIT = (
    "Agent 'agent' failed with code 1: ActionRequiredError: You've hit your usage limit "
    "You've saved $3501 on API model usage this month with Ultra. Switch to a different "
    "model or set a Spend Limit to continue with Auto. Your usage limits will reset when "
    "your monthly cycle ends on 10/30/2026."
)
CHAIN = ("codex/a", "cursor/auto", "opencode/b")


def _controller(clock: FakeClock) -> RecoveryController:
    return RecoveryController(options=RecoveryControllerOptions(clock=clock))


def _bench_unproductive(
    controller: RecoveryController,
    agent: str,
    failure: BaseException | str = "kaboom",
    count: int = 3,
) -> None:
    for _ in range(count):
        record_resolver_failure(controller, agent, failure)


def test_fresh_chain_offers_highest_priority_candidate() -> None:
    choice = choose_resolver(_controller(FakeClock()), CHAIN)

    assert choice.agent == "codex/a"
    assert choice.wait_ms == 0


def test_failed_resolver_is_skipped_in_favour_of_next_candidate() -> None:
    controller = _controller(FakeClock())

    _bench_unproductive(controller, "codex/a", "kaboom: something nobody has a pattern for")

    assert choose_resolver(controller, CHAIN).agent == "cursor/auto"


def test_unrecognised_failure_text_still_cools_the_resolver_down() -> None:
    controller = _controller(FakeClock())

    _bench_unproductive(controller, "codex/a", "")

    assert choose_resolver(controller, ("codex/a",)).agent is None


def test_repeated_failures_back_off_longer_each_time() -> None:
    clock = FakeClock()
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock, agent_max_total_unproductive=15)
    )
    waits: list[int] = []
    for _ in range(3):
        _bench_unproductive(controller, "codex/a", "kaboom")
        waits.append(choose_resolver(controller, ("codex/a",)).wait_ms)
        clock.advance(waits[-1] / 1000.0)

    assert waits[0] < waits[1] < waits[2]


def test_cursor_usage_limit_text_cools_down_for_a_long_time() -> None:
    controller = _controller(FakeClock())

    _bench_unproductive(controller, "cursor/auto", CURSOR_USAGE_LIMIT)

    choice = choose_resolver(controller, ("cursor/auto",))
    assert choice.agent is None
    assert choice.wait_ms >= 60_000


def test_every_candidate_cooling_down_reports_the_earliest_wait() -> None:
    controller = _controller(FakeClock())
    _bench_unproductive(controller, "codex/a", "kaboom")
    _bench_unproductive(controller, "cursor/auto", CURSOR_USAGE_LIMIT)
    _bench_unproductive(controller, "opencode/b", "kaboom")

    choice = choose_resolver(controller, CHAIN)

    assert choice.agent is None
    assert choice.wait_ms > 0


def test_candidate_returns_once_its_cooldown_elapses() -> None:
    clock = FakeClock()
    controller = _controller(clock)
    _bench_unproductive(controller, "codex/a", "kaboom")
    wait_ms = choose_resolver(controller, ("codex/a",)).wait_ms

    clock.advance(wait_ms / 1000.0)

    assert choose_resolver(controller, ("codex/a",)).agent == "codex/a"


def test_verified_success_clears_the_resolver_history() -> None:
    controller = _controller(FakeClock())
    _bench_unproductive(controller, "codex/a", "kaboom")

    record_resolver_success(controller, "codex/a")

    assert choose_resolver(controller, ("codex/a",)).agent == "codex/a"


def test_empty_chain_offers_nothing_and_does_not_wait() -> None:
    choice = choose_resolver(_controller(FakeClock()), ())

    assert choice.agent is None
    assert choice.wait_ms == 0


@pytest.mark.parametrize("failure", [ValueError("boom"), "boom", ""])
def test_cooldown_accepts_any_failure_shape(failure: BaseException | str) -> None:
    controller = _controller(FakeClock())

    _bench_unproductive(controller, "codex/a", failure)

    assert choose_resolver(controller, ("codex/a",)).agent is None


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_unrecognised_failure_text_still_cools_the_resolver_down": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_unrecognised_failure_text_still_benches_the_resolver with an unchanged body"
    ),
    "test_cursor_usage_limit_text_cools_down_for_a_long_time": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_cursor_usage_limit_text_benches_for_a_long_time with an unchanged body"
    ),
    "test_every_candidate_cooling_down_reports_the_earliest_wait": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_every_candidate_benched_reports_the_earliest_wait with an unchanged body"
    ),
    "test_cooldown_accepts_any_failure_shape": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_bench_accepts_any_failure_shape with an unchanged body"
    ),
    "test_integration_health_does_not_contaminate_development_phase": (
        "operator rule: an agent is either on cooldown or not; the cooldown is keyed by agent "
        "only, so a resolution cooldown also keeps the agent out of development until it "
        "expires, and the test asserts the shared cooldown and its expiry"
    ),
    "test_development_phase_failure_does_not_contaminate_resolution": (
        "operator rule: an agent is either on cooldown or not; the cooldown is keyed by agent "
        "only, so a development unproductive_limit cooldown also keeps the agent out of "
        "resolution until it expires, and the test asserts the shared cooldown and its expiry"
    ),
    "test_resolution_cooldown_applies_in_every_phase_until_it_expires": (
        "operator rule: an agent is either on cooldown or not; replaces the phase isolation "
        "test with the agent-keyed cooldown contract"
    ),
    "test_development_cooldown_applies_to_resolution_until_it_expires": (
        "operator rule: an agent is either on cooldown or not; replaces the phase isolation "
        "test with the agent-keyed cooldown contract"
    ),
}


def test_resolution_cooldown_applies_in_every_phase_until_it_expires() -> None:
    clock = FakeClock()
    controller = _controller(clock)

    _bench_unproductive(controller, "codex/a", "kaboom")

    assert choose_resolver(controller, CHAIN).agent == "cursor/auto"
    assert controller.preferred_agent_index("development", CHAIN).agent == "cursor/auto"
    wait_ms = choose_resolver(controller, ("codex/a",)).wait_ms
    assert wait_ms > 0

    clock.advance(wait_ms / 1000.0)

    assert choose_resolver(controller, CHAIN).agent == "codex/a"
    assert controller.preferred_agent_index("development", CHAIN).agent == "codex/a"


def test_development_cooldown_applies_to_resolution_until_it_expires() -> None:
    from ralph.recovery.agent_failure_ledger import AttemptFact, Cooldown

    clock = FakeClock()
    controller = _controller(clock)

    for _ in range(3):
        controller.ledger.record(
            AttemptFact(
                phase="development",
                agent="codex/a",
                artifact_valid=False,
            )
        )

    status = controller.ledger.status("development", "codex/a")
    assert isinstance(status, Cooldown)
    assert status.reason == "unproductive_limit"
    assert controller.preferred_agent_index("development", CHAIN).agent == "cursor/auto"
    assert choose_resolver(controller, CHAIN).agent == "cursor/auto"
    wait_ms = choose_resolver(controller, ("codex/a",)).wait_ms
    assert wait_ms > 0

    clock.advance(wait_ms / 1000.0)

    assert choose_resolver(controller, CHAIN).agent == "codex/a"
    assert controller.preferred_agent_index("development", CHAIN).agent == "codex/a"
