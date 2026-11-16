from __future__ import annotations

import pytest

from ralph.pipeline.resolution_outcome import (
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
)
from tests._scenario_agent_mode import AgentMode
from tests._scenario_display import ScenarioDisplay
from tests._scenario_git import commit_file, git
from tests._scenario_operator_line import OperatorLine
from tests._scenario_result import ScenarioResult
from tests.scenarios._paused_rebase_fixtures import (
    paused_text_and_gitlink_rebase,
    paused_text_rebase,
)
from tests.scenarios._rebase_recovery_harness_helpers import (
    FakeAgentRunner,
    capture_operator_lines,
    corrupted_config,
    scenario_config,
    supervisor_restart,
)

pytestmark = pytest.mark.subprocess_e2e

__all__ = [
    "SCENARIO_FILES",
    "AgentMode",
    "FakeAgentRunner",
    "OperatorLine",
    "ResolvedOutcome",
    "RetryLaterOutcome",
    "ScenarioDisplay",
    "ScenarioResult",
    "StopResolutionOutcome",
    "capture_operator_lines",
    "commit_file",
    "corrupted_config",
    "git",
    "paused_text_and_gitlink_rebase",
    "paused_text_rebase",
    "scenario_config",
    "supervisor_restart",
]

SCENARIO_FILES: tuple[str, ...] = ("tests/scenarios/test_integration_scenario_matrix.py",)
