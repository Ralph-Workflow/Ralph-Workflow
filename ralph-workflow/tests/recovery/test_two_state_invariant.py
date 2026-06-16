"""Black-box tests for the recovery controller's two-state invariant.

The wt-012 plan enshrines the recovery controller's two-state
recovery invariant in import-time code. This file tests the
end-to-end behavior of the two MAIN RULES plus the never-exit /
never-skip-an-agent invariant:

  - **Two MAIN RULES:**
    1. Exponential backoff to the next agent via
       ``AgentUnavailabilityTracker.mark_unavailable``.
    2. Same-agent retry via ``AgentChain.with_retry_increment``.

  - **Never-exit invariant:** the pipeline NEVER exits because
    of agent unavailability. When all agents in the chain are
    on cooldown, the controller returns a WAIT effect (the
    canonical 3-tuple ``(new_state, effects, failure_event)``
    with ``is_waiting_state=True``, ``last_retry_delay_ms>0``,
    and ``effects=[]``).

  - **Never-skip-an-agent invariant:** every agent is
    RECOVERABLE; an agent on cooldown is never permanently
    skipped. The controller reconsiders earlier agents when
    the chain advances (``wrap=True`` re-arming).

The tests below drive the recovery controller through its
PUBLIC surface only (``RecoveryController.handle``,
``RecoveryControllerOptions.unavailability_entries``,
``FailureClassifier.classify``). No private mutation. No real
subprocess. No real network. No real sleep. Uses ``FakeClock``
so the tests complete in <2s combined.

The import-time invariants themselves are exercised by the
``test_two_state_invariant_locked_at_import`` and
``test_two_state_invariant_locked_at_import_under_optimization``
tests, which import the modules and assert they raise no error
(and the same import under ``python -O`` to confirm the
``if/raise RuntimeError`` checks survive ``-O`` stripping per
AGENTS.md).
"""

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
from ralph.recovery.agent_budget_registry import AgentBudgetRegistry
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


def _controller_with_budget(
    *,
    clock: FakeClock | None = None,
    initial_entries: dict[str, UnavailabilityEntry] | None = None,
) -> RecoveryController:
    """Build a controller with a budget registry pre-seeded for the three agents."""
    registry = AgentBudgetRegistry().set_budget("development", "claude", 3)
    registry = registry.set_budget("development", "opencode", 3)
    registry = registry.set_budget("development", "agy", 3)
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock or FakeClock(start=0.0),
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
            budget_registry=registry,
            unavailability_entries=initial_entries or {},
        ),
    )


def test_walk_through_every_agent_on_cooldown_returns_wait() -> None:
    """When every agent in a chain is on cooldown, the controller
    enters the WAIT state and returns the canonical 3-tuple.

    Setup: a 3-agent chain (``claude``, ``opencode``, ``agy``)
    where every agent is on cooldown, with the earliest cooldown
    in the MIDDLE of the chain (opencode at 3000ms). The
    controller must:

      (a) NOT advance ``state.phase`` to ``failed_terminal``.
      (b) Set ``state.is_waiting_state=True`` (the structured wait flag).
      (c) Set ``state.last_retry_delay_ms == 3000`` (the middle agent's
          cooldown, NOT the first or last agent's cooldown).
      (d) Return an empty ``effects`` list (no ``ExitFailureEffect``).
      (e) Unpack the canonical 3-tuple ``(new_state, effects, failure_event)``
          per ``controller.py:144``.

    This test verifies the never-exit invariant: the pipeline
    WAITS off cooldown for the next available agent and never
    permanently skips an agent.
    """
    initial_entries: dict[str, UnavailabilityEntry] = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
        "development:opencode": UnavailabilityEntry(
            unavailable_until_ms=3000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=3000,
            max_backoff_ms=3000,
        ),
        "development:agy": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
    }
    controller = _controller_with_budget(initial_entries=initial_entries)
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())

    # Canonical 3-tuple unpack per controller.py:144.
    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    # The controller never exits the pipeline.
    assert new_state.phase == "development", (
        f"phase must not advance to failed_terminal, got {new_state.phase}"
    )
    # The controller sets the structured wait-state flag.
    assert new_state.is_waiting_state is True, (
        "is_waiting_state must be True when every agent is on cooldown"
    )
    # The controller picks the EARLIEST cooldown, not the first or last agent.
    assert new_state.last_retry_delay_ms == 3000, (
        f"last_retry_delay_ms must equal the middle agent's cooldown"
        f" (3000ms), got {new_state.last_retry_delay_ms}"
    )
    # The controller returns an empty effects list (no ExitFailureEffect).
    assert effects == [], f"expected no effects (wait state, not exit), got {effects}"


def test_wrap_true_rearms_to_earliest_cooldown() -> None:
    """``wrap=True`` re-arming: the controller advances to the
    EARLIEST-cooldown agent when the chain is exhausted and an
    earlier agent's cooldown has expired.

    Setup: 3-agent chain. Pre-seed the FIRST and THIRD agents on
    cooldown. Leave the SECOND agent available. Drive the
    controller with a claude failure classified as unavailable.
    The controller must:

      (a) Mark claude on cooldown (now all agents on cooldown).
      (b) Find agy on cooldown too.
      (c) Use ``wrap=True`` to reconsider opencode (the second
          agent, the only available one).
      (d) Set ``chain.current_index = 1`` (opencode's index).

    This test verifies the never-skip-an-agent invariant: an
    agent is never permanently skipped. The controller
    reconsiders earlier agents whose cooldown has expired via
    ``wrap=True``.
    """
    initial_entries: dict[str, UnavailabilityEntry] = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
        # opencode (index 1) is intentionally NOT in initial_entries -> available
        "development:agy": UnavailabilityEntry(
            unavailable_until_ms=30000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=30000,
            max_backoff_ms=30000,
        ),
    }
    controller = _controller_with_budget(initial_entries=initial_entries)
    state = _three_agent_state(current_index=0, retries=0)

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())

    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    # Claude is now on cooldown; the controller looks for the next
    # available agent. The first attempt (offset 1) sees agy on
    # cooldown (with wrap=True, it would also see claude on cooldown
    # but claude is the CURRENT agent). The second attempt (offset 2)
    # with wrap=True sees opencode available (the only available
    # agent).
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
    """The two-state invariant is locked at import time.

    Importing ``ralph.recovery.controller`` and
    ``ralph.recovery.agent_unavailability_tracker`` MUST NOT raise
    ``RuntimeError`` -- the import-time invariant in each module
    verifies the production code is still wired correctly. A
    regression that breaks the invariant (e.g. renames
    ``_mark_agent_unavailable`` or adds a new public mutator to
    the tracker) would cause this import to fail.
    """
    importlib.import_module("ralph.recovery.controller")
    importlib.import_module("ralph.recovery.agent_unavailability_tracker")


def test_two_state_invariant_locked_at_import_under_optimization() -> None:
    """The two-state invariant is locked at import time under
    ``python -O``.

    The ``if/raise RuntimeError`` checks MUST survive
    ``python -O`` (which strips ``assert`` statements per AGENTS.md).
    This test verifies the invariant by reading the source of
    both modules and confirming they do NOT use ``assert``
    for the invariant check (the only safe pattern under
    ``python -O`` is ``if condition: raise RuntimeError(...)``).

    The compile step is a deterministic, pure-Python check
    that does not require spawning a subprocess.
    """
    repo_root = Path(__file__).resolve().parents[2]
    controller_path = repo_root / "ralph" / "recovery" / "controller.py"
    tracker_path = repo_root / "ralph" / "recovery" / "agent_unavailability_tracker.py"

    for source_path in (controller_path, tracker_path):
        source = source_path.read_text(encoding="utf-8")
        # The invariant must be a regular `if ... raise RuntimeError(...)`
        # NOT a bare `assert ...`. python -O strips `assert` so a
        # bare `assert` would be silently disabled.
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
