from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.recovery.agent_failure_ledger import Cooldown, Eligible
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.timeout_defaults import AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT
from tests.recovery.test_cooldown_real_invocation_main_phases import (
    _MainPhaseHarness,
    _production_phases,
)
from tests.recovery.test_planning_overload_rotation import _run_steps

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("phase", _production_phases())
def test_three_clean_exits_without_the_artifact_cool_the_agent_in_every_phase(
    phase: str,
    tmp_git_repo: Path,
) -> None:
    harness = _MainPhaseHarness(phase, tmp_git_repo)
    limit = AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT

    _run_steps(harness, limit - 1)
    assert isinstance(harness.controller.agent_eligibility(phase, harness.primary), Eligible)

    _run_steps(harness, 1)
    status = harness.controller.agent_eligibility(phase, harness.primary)
    assert isinstance(status, Cooldown), (phase, harness.invoked, status)
    assert status.reason == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)

    assert harness.invoked == [harness.primary] * limit
    assert harness.controller.preferred_agent_index(phase, harness.agents).agent != harness.primary
