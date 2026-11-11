from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _no_output_opts() -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _three_agent_state(current_index: int = 0, retries: int = 0) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=retries,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def _controller(
    *,
    clock: FakeClock | None = None,
    initial_entries: dict[str, UnavailabilityEntry] | None = None,
) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock or FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            unavailability_entries=initial_entries or {},
        ),
    )


def test_walk_through_every_agent_on_cooldown_returns_wait() -> None:
    initial_entries: dict[str, UnavailabilityEntry] = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
        "opencode": UnavailabilityEntry(
            unavailable_until_ms=3000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=3000,
            max_backoff_ms=3000,
        ),
        "agy": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
    }
    controller = _controller(initial_entries=initial_entries)
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())

    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development", (
        f"phase must not advance to failed_terminal, got {new_state.phase}"
    )
    assert new_state.is_waiting_state is True, (
        "is_waiting_state must be True when every agent is on cooldown"
    )
    assert new_state.last_retry_delay_ms == 3000, (
        f"last_retry_delay_ms must equal the middle agent's cooldown"
        f" (3000ms), got {new_state.last_retry_delay_ms}"
    )
    assert effects == [], f"expected no effects (wait state, not exit), got {effects}"


def test_wrap_true_rearms_to_earliest_cooldown() -> None:
    initial_entries: dict[str, UnavailabilityEntry] = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
        "agy": UnavailabilityEntry(
            unavailable_until_ms=30000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=30000,
            max_backoff_ms=30000,
        ),
    }
    controller = _controller(initial_entries=initial_entries)
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())

    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1, (
        f"chain must advance to the only available agent (opencode, index 1),"
        f" got current_index={chain.current_index}"
    )
    assert new_state.is_waiting_state is False, (
        "is_waiting_state must be False (we found an available agent)"
    )
    assert effects == [], f"expected no effects, got {effects}"


def test_two_state_invariant_locked_at_import() -> None:
    importlib.import_module("ralph.recovery.controller")
    importlib.import_module("ralph.recovery.agent_unavailability_tracker")


def test_two_state_invariant_locked_at_import_under_optimization() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    controller_path = repo_root / "ralph" / "recovery" / "controller.py"
    tracker_path = repo_root / "ralph" / "recovery" / "agent_unavailability_tracker.py"

    for source_path in (controller_path, tracker_path):
        source = source_path.read_text(encoding="utf-8")
        if "if " not in source:
            raise AssertionError(
                f"{source_path} must contain an `if` check (the invariant is"
                f" enforced via `if condition: raise RuntimeError(...)`, not `assert`)"
            )
        if "raise RuntimeError(" not in source:
            raise AssertionError(
                f"{source_path} must contain a `raise RuntimeError(...)` (the"
                f" invariant must be enforced via if/raise, not assert)"
            )


def test_never_exit_invariant_locked_at_import_under_python_o() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    controller_path = repo_root / "ralph" / "recovery" / "controller.py"
    source = controller_path.read_text(encoding="utf-8")

    if "def _assert_never_exit_invariant" not in source:
        raise AssertionError(
            "ralph/recovery/controller.py must define"
            " _assert_never_exit_invariant. The never-exit invariant is"
            " enforced at import time via the if/raise RuntimeError"
            " idiom; missing the function silently disables the"
            " enshrinement. Add the function in the same commit as the"
            " wt-012 plan."
        )

    if "_assert_never_exit_invariant()" not in source:
        raise AssertionError(
            "ralph/recovery/controller.py must call"
            " _assert_never_exit_invariant() at module level. The"
            " import-time check must run on every module import. Add the"
            " call after the function definition in the same commit as"
            " the wt-012 plan."
        )

    func_start = source.index("def _assert_never_exit_invariant")
    next_def_markers = (
        "\ndef ",
        "\nclass ",
    )
    func_end = len(source)
    for marker in next_def_markers:
        idx = source.find(marker, func_start + 1)
        if idx != -1 and idx < func_end:
            func_end = idx
    func_body = source[func_start:func_end]

    if "if " not in func_body:
        raise AssertionError(
            "ralph/recovery/controller.py:_assert_never_exit_invariant"
            " body must contain an `if` check. The invariant must be"
            " enforced via `if condition: raise RuntimeError(...)`, not"
            " via bare `assert` (which python -O would strip)."
        )
    if "raise RuntimeError(" not in func_body:
        raise AssertionError(
            "ralph/recovery/controller.py:_assert_never_exit_invariant"
            " body must contain a `raise RuntimeError(...)`. The"
            " invariant must be enforced via `if/raise RuntimeError(...)`,"
            " not via bare `assert` (which python -O would strip)."
        )
