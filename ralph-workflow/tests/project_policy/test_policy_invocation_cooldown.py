from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner import build_entry_point_recovery_controller
from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.recovery.attempt_facts import AttemptFacts


def test_project_policy_owner_reports_cooldown_selection_and_wait() -> None:
    clock = FakeClock()
    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.PROJECT_POLICY,
        config=None,
        policy_bundle=None,
        deps=AttemptRunnerDeps(
            clock=clock,
            invoke=lambda *_args, **_kwargs: None,
            probe_runner=None,
            workspace_probe=None,
            connectivity=None,
        ),
    )
    agents = ["broken", "healthy"]
    controller.register_phase_chain("policy_remediation", agents)

    for _attempt in range(3):
        controller.finalize_attempt(
            "policy_remediation",
            "broken",
            facts=AttemptFacts(artifact_valid=False, workspace_changed=False),
            attempt_failed=True,
        )

    eligibility = dict(controller.chain_eligibility("policy_remediation", agents))
    selection = controller.preferred_agent_index("policy_remediation", agents)
    wait_ms = controller.earliest_available_wait_ms("policy_remediation", ["broken"])

    assert isinstance(eligibility["broken"], Cooldown)
    assert selection.agent == "healthy"
    assert wait_ms > 0
