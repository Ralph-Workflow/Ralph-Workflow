"""B1-B6: conflict resolution must follow the pipeline chain, not a local loop."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
)
from tests._conflict_resolution_phase_parity_seams import (
    _config,
    _install_seams,
    _policy_bundle,
)

if TYPE_CHECKING:
    import pytest


def test_conflict_waits_for_earliest_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
    from ralph.recovery.unavailability_reason import UnavailabilityReason

    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            unavailability_entries={
                name: UnavailabilityEntry(
                    unavailable_until_ms=delay,
                    reason=UnavailabilityReason.NO_OUTPUT_AT_START,
                    attempt=0,
                    base_backoff_ms=5000,
                    max_backoff_ms=18000000,
                )
                for name, delay in (("one", 9000), ("two", 5000))
            },
        )
    )
    monkeypatch.setattr(driver_module, "resolution_chain_agents", lambda _bundle: ("one", "two"))
    monkeypatch.setattr(driver_module, "_sleep_seconds", clock.advance)
    _install_seams(monkeypatch, surviving_per_round=[[]])
    called: list[str] = []

    def invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        called.append(agent_name)
        return True

    assert run_conflict_resolution_pipeline(
        root=tmp_path,
        target="main",
        config=_config(),
        pipeline_deps=None,
        workspace_scope=None,
        policy_bundle=_policy_bundle(),
        display=None,
        display_context=None,
        invoke=invoke,
        session=ResolutionSession(recovery_controller=controller),
    )
    assert called == ["two"]
    assert clock.monotonic() == 5.0
    assert (
        controller.preferred_agent_index("rebase_conflict_resolution", ["one", "two"]).agent
        == "two"
    )
