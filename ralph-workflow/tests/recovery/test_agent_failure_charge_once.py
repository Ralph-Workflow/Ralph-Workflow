from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING
from unittest.mock import patch

from ralph.agents.invoke import AgentInactivityTimeoutError
from ralph.config.models import UnifiedConfig
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.probe import DefaultWorkspaceChangeProbe
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    ScenarioOutcome,
    run_scenario,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import (
        ArtifactRequirementsResolverFn,
        PipelineDeps,
    )
    from ralph.policy.models import PolicyBundle
    from ralph.pro_support.hooks import RecoveryControllerFactory

SHIPPED_SAME_AGENT_RETRIES = 10

MINIMAL_SAME_AGENT_RETRIES = 1

EXPECTED_TOTAL_LIMIT = 9

_DEMOTION_MARKER = "demoted in phase 'development'"

_TOTAL_PATTERN = re.compile(r"total=(\d+)")


def _failing_agent_script(
    agent: str,
    _phase: str,
    _attempt: int,
    _workspace: object,
) -> AgentAttemptResult:
    if agent == "agent_b":
        return AgentAttemptResult(produce_artifact=True)
    return AgentAttemptResult(
        modify_workspace=True,
        produce_artifact=False,
        raise_error=AgentInactivityTimeoutError(agent, 60.0),
    )


def _model_validate_with_same_agent_retries(
    same_agent_retries: int,
) -> Callable[..., UnifiedConfig]:
    original = UnifiedConfig.model_validate

    def validate(
        payload: Mapping[str, object],
        /,
        *args: object,
        **kwargs: object,
    ) -> UnifiedConfig:
        general = payload.get("general")
        if isinstance(general, Mapping):
            updated = dict(payload)
            updated["general"] = {
                **general,
                "max_same_agent_retries": same_agent_retries,
            }
            return original(updated, *args, **kwargs)
        return original(payload, *args, **kwargs)

    return validate


def _deps_factory_with_run_ledger(
    controller: RecoveryController,
) -> Callable[..., PipelineDeps]:

    def factory(
        display_context: DisplayContext,
        *,
        recovery_controller_factory: RecoveryControllerFactory | None = None,
        policy_bundle: PolicyBundle | None = None,
        registry_factory: Callable[[UnifiedConfig], object] | None = None,
        artifact_requirements_resolver: ArtifactRequirementsResolverFn | None = None,
    ) -> PipelineDeps:
        deps = make_test_pipeline_deps(
            display_context,
            recovery_controller_factory=recovery_controller_factory,
            policy_bundle=policy_bundle,
            registry_factory=registry_factory,
            artifact_requirements_resolver=artifact_requirements_resolver,
        )
        return dataclasses.replace(
            deps,
            ledger=controller.ledger,
            unavailability_store=controller.unavailability_store,
            recovery_controller=controller,
        )

    return factory


def _run_executor_route_scenario(
    tmp_path: Path,
    *,
    same_agent_retries: int,
) -> ScenarioOutcome:
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            workspace_probe=DefaultWorkspaceChangeProbe(),
        )
    )
    with (
        patch.object(
            UnifiedConfig,
            "model_validate",
            _model_validate_with_same_agent_retries(same_agent_retries),
        ),
        patch(
            "tests.recovery._scenario_harness.make_test_pipeline_deps",
            _deps_factory_with_run_ledger(controller),
        ),
    ):
        return run_scenario(
            tmp_path,
            script=_failing_agent_script,
            agents=("agent_a", "agent_b"),
            controller=controller,
            max_iterations=25,
        )


def _demotion_totals(logs: list[str]) -> list[int]:
    totals: list[int] = []
    for line in logs:
        if _DEMOTION_MARKER not in line:
            continue
        match = _TOTAL_PATTERN.search(line)
        if match is not None:
            totals.append(int(match.group(1)))
    return totals


def test_scenario_executor_recovery_route_demotes_at_nine_total_attempts(
    tmp_path: Path,
) -> None:
    outcome = _run_executor_route_scenario(
        tmp_path,
        same_agent_retries=SHIPPED_SAME_AGENT_RETRIES,
    )

    assert outcome.invocations == ["agent_a"] * EXPECTED_TOTAL_LIMIT + ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0

    assert any("total_unproductive_limit_reached" in line for line in outcome.logs), (
        "the demotion must be logged with the reason it was reached"
    )


def test_scenario_executor_recovery_route_charges_each_physical_failure_once(
    tmp_path: Path,
) -> None:
    outcome = _run_executor_route_scenario(
        tmp_path,
        same_agent_retries=MINIMAL_SAME_AGENT_RETRIES,
    )

    physical_failures = outcome.invocations.count("agent_a")
    assert outcome.invocations == ["agent_a"] * EXPECTED_TOTAL_LIMIT + ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0

    assert _demotion_totals(outcome.logs) == [EXPECTED_TOTAL_LIMIT]
    assert physical_failures == EXPECTED_TOTAL_LIMIT


def test_recovery_regression_executor_failure_is_charged_once(
    tmp_path: Path,
) -> None:
    outcome = _run_executor_route_scenario(
        tmp_path,
        same_agent_retries=MINIMAL_SAME_AGENT_RETRIES,
    )

    assert outcome.invocations == ["agent_a"] * EXPECTED_TOTAL_LIMIT + ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0
    assert any("total_unproductive_limit_reached" in line for line in outcome.logs), (
        "the demotion must be logged with the reason it was reached"
    )
