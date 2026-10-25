"""Black-box tests for the shared integration-resolver health ledger.

A resolver that exits without a verified result must stop being offered,
whatever its error text says; repeating the same failing call is not recovery.
"""

from __future__ import annotations

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution.resolver_health import (
    bench_resolver,
    choose_resolver,
    clear_resolver,
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


def test_fresh_chain_offers_highest_priority_candidate() -> None:
    choice = choose_resolver(_controller(FakeClock()), CHAIN)

    assert choice.agent == "codex/a"
    assert choice.wait_ms == 0


def test_failed_resolver_is_skipped_in_favour_of_next_candidate() -> None:
    controller = _controller(FakeClock())

    bench_resolver(controller, "codex/a", "kaboom: something nobody has a pattern for")

    assert choose_resolver(controller, CHAIN).agent == "cursor/auto"


def test_unrecognised_failure_text_still_benches_the_resolver() -> None:
    controller = _controller(FakeClock())

    bench_resolver(controller, "codex/a", "")

    assert choose_resolver(controller, ("codex/a",)).agent is None


def test_repeated_failures_back_off_longer_each_time() -> None:
    clock = FakeClock()
    controller = _controller(clock)
    waits: list[int] = []
    for _ in range(3):
        bench_resolver(controller, "codex/a", "kaboom")
        waits.append(choose_resolver(controller, ("codex/a",)).wait_ms)
        clock.advance(waits[-1] / 1000.0)

    assert waits[0] < waits[1] < waits[2]


def test_cursor_usage_limit_text_benches_for_a_long_time() -> None:
    controller = _controller(FakeClock())

    bench_resolver(controller, "cursor/auto", CURSOR_USAGE_LIMIT)

    choice = choose_resolver(controller, ("cursor/auto",))
    assert choice.agent is None
    assert choice.wait_ms >= 60_000


def test_every_candidate_benched_reports_the_earliest_wait() -> None:
    controller = _controller(FakeClock())
    bench_resolver(controller, "codex/a", "kaboom")
    bench_resolver(controller, "cursor/auto", CURSOR_USAGE_LIMIT)
    bench_resolver(controller, "opencode/b", "kaboom")

    choice = choose_resolver(controller, CHAIN)

    assert choice.agent is None
    assert 0 < choice.wait_ms < 60_000


def test_candidate_returns_once_its_cooldown_elapses() -> None:
    clock = FakeClock()
    controller = _controller(clock)
    bench_resolver(controller, "codex/a", "kaboom")
    wait_ms = choose_resolver(controller, ("codex/a",)).wait_ms

    clock.advance(wait_ms / 1000.0)

    assert choose_resolver(controller, ("codex/a",)).agent == "codex/a"


def test_verified_success_clears_the_resolver_history() -> None:
    controller = _controller(FakeClock())
    bench_resolver(controller, "codex/a", "kaboom")

    clear_resolver(controller, "codex/a")

    assert choose_resolver(controller, ("codex/a",)).agent == "codex/a"


def test_empty_chain_offers_nothing_and_does_not_wait() -> None:
    choice = choose_resolver(_controller(FakeClock()), ())

    assert choice.agent is None
    assert choice.wait_ms == 0


@pytest.mark.parametrize("failure", [ValueError("boom"), "boom", ""])
def test_bench_accepts_any_failure_shape(failure: BaseException | str) -> None:
    controller = _controller(FakeClock())

    bench_resolver(controller, "codex/a", failure)

    assert choose_resolver(controller, ("codex/a",)).agent is None


def test_integration_health_does_not_contaminate_development_phase() -> None:
    """Demoting an agent in resolution does not affect its status in development."""
    controller = _controller(FakeClock())

    bench_resolver(controller, "codex/a", "kaboom")

    # In resolution phase, codex/a is cooling down.
    res_choice = choose_resolver(controller, CHAIN)
    assert res_choice.agent == "cursor/auto"

    # In development phase, codex/a remains fully eligible.
    dev_choice = controller.preferred_agent_index("development", CHAIN)
    assert dev_choice.agent == "codex/a"


def test_development_phase_failure_does_not_contaminate_resolution() -> None:
    """Failures recorded in development do not contaminate resolution selection."""
    from ralph.recovery.agent_failure_ledger import AttemptFact

    controller = _controller(FakeClock())

    # Demote codex/a in development phase.
    for _ in range(3):
        controller.ledger.record(
            AttemptFact(
                phase="development",
                agent="codex/a",
                artifact_valid=False,
            )
        )

    # In development phase, codex/a is in cooldown.
    dev_choice = controller.preferred_agent_index("development", CHAIN)
    assert dev_choice.agent == "cursor/auto"

    # In resolution phase, codex/a is completely unaffected and eligible.
    res_choice = choose_resolver(controller, CHAIN)
    assert res_choice.agent == "codex/a"
