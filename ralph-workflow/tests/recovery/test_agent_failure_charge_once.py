"""Public-seam scenario tests for the agent-failure single-owner contract.

PRODUCT_CRITERIA §3.4 (exactly ONE component decides that an agent has
failed too many times), §4.2/§4.3 (the limits: 3 consecutive unproductive
attempts put an agent in cooldown, 9 total unproductive attempts demote it)
and §6 (the public seam: a scripted fake agent, a temporary workspace, a
fake clock, and assertions on observable behaviour only).

THE SEAM
--------
These tests reuse the shared scenario harness
(:mod:`tests.recovery._scenario_harness`): the scripted agent writes into a
real temporary workspace, the production workspace-change probe
(:class:`ralph.workspace.probe.DefaultWorkspaceChangeProbe`) observes the
change, and the assertions are made on what is observable from outside --
which agents ran and in what order, how the run ended, and what the run
logged. No ledger counter is read, no private attribute is inspected, and no
production BEHAVIOUR is faked: the two seams touched below are the public
configuration validator (which only sets the documented
``general.max_same_agent_retries`` knob) and the harness' dependency
factory (which receives the run's ledger exactly the way production wires
it).

Two harness knobs have to move for the EXECUTOR's recovery route to be
reachable, and both moves are production wiring rather than test
instrumentation:

1. ``general.max_same_agent_retries`` must be greater than zero.
   :func:`ralph.pipeline.effect_executor.recovery_attempts_for_effect`
   derives the executor's same-agent recovery budget from it, and
   :func:`ralph.pipeline.effect_executor.build_agent_recovery_plan`
   returns ``None`` while that budget is zero -- so with the harness'
   default of ``0`` the executor never produces a recovery plan and never
   charges the owner. The shipped default is ``10``
   (:class:`ralph.config.general_config.GeneralConfig`), and this module
   uses both that default and the smallest budget that reaches the route
   (``1``).

2. The pipeline dependencies must carry the run's failure ledger.
   :func:`ralph.pipeline.run_loop._with_run_ledger` replaces
   ``PipelineDeps.ledger`` with ``RecoveryController.ledger`` so the
   executor's recovery route and the reducer's ordinary route charge the
   SAME owner. The harness builds its dependencies without that ledger, so
   this module performs the same replacement.

THE SINGLE-CHARGE CONTRACT
--------------------------
The executor's recovery route and the reducer's ordinary agent-failure
route are mutually exclusive charge routes for one physical failure:

* the executor charges attempts it sees inside the recovery attempt
  handler, and marks the attempt-scoped ``AgentFailureEvent`` with
  ``already_charged=True``; and
* the reducer charges an ordinary failure only when that explicit fact is
  absent.

Both routes land on the SAME owner: the run's ledger IS the recovery
controller's ledger (``ralph/pipeline/run_loop.py:3434``,
``_with_run_ledger``). The explicit event fact therefore makes the two
routes mutually exclusive: every physical failure is charged exactly
once, whether the executor or the reducer owns that route.

The observable contract is that agent A is demoted after exactly nine
physical attempts and the owner reports nine charges for those nine
failures. The tests below cover both the shipped recovery budget and the
smallest budget that reaches the executor's recovery route.
"""

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

#: The shipped default for the executor's same-agent recovery budget
#: (``GeneralConfig.max_same_agent_retries``).
SHIPPED_SAME_AGENT_RETRIES = 10

#: The smallest budget that still reaches the executor's recovery route.
MINIMAL_SAME_AGENT_RETRIES = 1

#: The total limit PRODUCT_CRITERIA §4.3 sets for demotion.
EXPECTED_TOTAL_LIMIT = 9

#: The demotion line the single failure owner reports in the run transcript.
_DEMOTION_MARKER = "demoted in phase 'development'"

_TOTAL_PATTERN = re.compile(r"total=(\d+)")


def _failing_agent_script(
    agent: str,
    _phase: str,
    _attempt: int,
    _workspace: object,
) -> AgentAttemptResult:
    """Script the failing agent and its fallover.

    Agent A modifies the workspace on every attempt and then fails with an
    inactivity timeout: each attempt is productive-but-incomplete, so the
    consecutive count restarts every time while the total count keeps
    growing. Agent B completes the phase.
    """
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
    """Return a config validator that pins the executor's recovery budget.

    The harness builds its configuration with ``max_same_agent_retries: 0``,
    which disables the executor's same-agent recovery route entirely. This
    wrapper changes NOTHING else about the configuration: it only sets the
    documented ``general.max_same_agent_retries`` knob.
    """
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
    """Return a dependency factory that hands the run's ledger to the pipeline.

    This is the wiring production performs in
    :func:`ralph.pipeline.run_loop._with_run_ledger`: the pipeline
    dependencies carry the run controller's own failure ledger, so the
    executor's recovery route and the reducer's ordinary route charge the
    SAME owner.
    """

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
        )

    return factory


def _run_executor_route_scenario(
    tmp_path: Path,
    *,
    same_agent_retries: int,
) -> ScenarioOutcome:
    """Run the failing-agent scenario through the executor's recovery route."""
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
    """Return the total counts the owner logged when it demoted the agent."""
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
    """§4.3: the executor's recovery route demotes the agent after nine attempts.

    With the shipped same-agent recovery budget the agent is invoked nine
    times, and the owner demotes it when its TOTAL limit is reached -- the
    agent is not demoted earlier, and the chain falls over to agent B,
    which completes the phase.

    Every assertion is on observable behaviour: the invocation sequence,
    the terminal outcome of the run, and the demotion the owner logged.
    """
    outcome = _run_executor_route_scenario(
        tmp_path,
        same_agent_retries=SHIPPED_SAME_AGENT_RETRIES,
    )

    assert outcome.invocations == ["agent_a"] * EXPECTED_TOTAL_LIMIT + ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0

    # The owner demoted the agent because its total limit was reached.
    assert any("total_unproductive_limit_reached" in line for line in outcome.logs), (
        "the demotion must be logged with the reason it was reached"
    )


def test_scenario_executor_recovery_route_charges_each_physical_failure_once(
    tmp_path: Path,
) -> None:
    """CHARACTERISATION of the corrected single-charge behaviour.

    The executor's recovery route and the reducer's ordinary agent-failure
    route are mutually exclusive charge routes for one physical failure.
    With the smallest recovery budget that reaches the executor route, the
    observable result is:

    * agent A is demoted after exactly nine physical attempts, and
    * the owner reports nine charges for those nine failures.

    These observable numbers prove that no physical failure was charged
    twice and that none was omitted.
    """
    outcome = _run_executor_route_scenario(
        tmp_path,
        same_agent_retries=MINIMAL_SAME_AGENT_RETRIES,
    )

    physical_failures = outcome.invocations.count("agent_a")
    assert outcome.invocations == ["agent_a"] * EXPECTED_TOTAL_LIMIT + ["agent_b"]
    assert outcome.final_state.phase == "complete"
    assert outcome.exit_code == 0

    # The owner's reported total matches the physical failures exactly.
    assert _demotion_totals(outcome.logs) == [EXPECTED_TOTAL_LIMIT]
    assert physical_failures == EXPECTED_TOTAL_LIMIT


def test_recovery_regression_executor_failure_is_charged_once(
    tmp_path: Path,
) -> None:
    """CONTRACT: one physical failure is charged to the owner exactly once.

    The agent must survive nine physical attempts (the §4.3 total limit) and
    the chain must fall over to agent B only then.

    Nine physical failures at a total limit of nine IS the "charged exactly
    once" proof: a second charge per failure would demote the agent after
    five attempts, and a missing charge would demote it later than nine.
    """
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
