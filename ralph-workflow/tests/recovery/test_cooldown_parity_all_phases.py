from __future__ import annotations

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions


@pytest.mark.parametrize("phase", ("development", PHASE_RESOLUTION))
def test_all_phases_share_finalization_and_cooldown(phase: str) -> None:
    clock = FakeClock()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            agent_max_consecutive_unproductive=2,
        )
    )

    for ordinal in range(2):
        controller.finalize_attempt(
            phase,
            "primary",
            facts=AttemptFacts(artifact_valid=False, workspace_changed=False),
            attempt_failed=True,
            attempt_key=f"{phase}:{ordinal}",
        )

    assert isinstance(controller.agent_eligibility(phase, "primary"), Cooldown)
