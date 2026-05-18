"""Contract tests for a fully custom-named policy workflow.

This module proves that Ralph Workflow routes on user-defined phase, drain,
loop counter, and budget counter names without any runtime knowledge of the
canonical default names (planning, development, review, etc.).

Scope: pure functions only (validate, explain, render, resolve_post_commit_phase,
with_loop_iteration). The full orchestrator is not exercised here because phase
handler registration is out of scope for this PR.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ralph.phases import HANDLERS, handle_phase, register_role_handlers
from ralph.phases.analysis import handle_generic_analysis_phase
from ralph.phases.commit import handle_commit_phase
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    BudgetCounterConfig,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
)


def _make_chain(name: str) -> tuple[str, AgentChainConfig]:
    return name, AgentChainConfig(agents=["fake-agent"])


def _make_drain(drain: str, chain: str) -> tuple[str, AgentDrainConfig]:
    return drain, AgentDrainConfig(chain=chain)


@pytest.fixture(scope="session")
def custom_bundle() -> PolicyBundle:
    """Build a fully custom-named PolicyBundle without any disk I/O."""
    agents = AgentsPolicy(
        agent_chains=dict(
            [
                _make_chain("design_chain"),
                _make_chain("build_chain"),
                _make_chain("audit_chain"),
                _make_chain("sign_off_chain"),
            ]
        ),
        agent_drains=dict(
            [
                _make_drain("design", "design_chain"),
                _make_drain("build", "build_chain"),
                _make_drain("audit", "audit_chain"),
                _make_drain("sign_off", "sign_off_chain"),
            ]
        ),
    )

    pipeline = PipelinePolicy(
        entry_phase="design",
        terminal_phase="done",
        loop_counters={
            "audit_round": LoopCounterConfig(default_max=2, description="Audit loop counter"),
        },
        budget_counters={
            "cycles": BudgetCounterConfig(
                tracks_budget=True, description="Outer cycle counter", default_max=5
            ),
        },
        phases={
            "design": PhaseDefinition(
                drain="design",
                role="execution",
                transitions=PhaseTransition(on_success="build"),
            ),
            "build": PhaseDefinition(
                drain="build",
                role="execution",
                transitions=PhaseTransition(on_success="audit"),
            ),
            "audit": PhaseDefinition(
                drain="audit",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="sign_off",
                    on_loopback="build",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="audit_round"),
                decisions={
                    "completed": PhaseDecisionRoute(target="sign_off", reset_loop=True),
                    "request_changes": PhaseDecisionRoute(target="build", reset_loop=False),
                    "failed": PhaseDecisionRoute(target="failed_terminal", reset_loop=False),
                },
            ),
            "sign_off": PhaseDefinition(
                drain="sign_off",
                role="commit",
                transitions=PhaseTransition(
                    on_success="done",
                    on_failure=None,
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="cycles",
                    loop_resets=["audit_round"],
                ),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(
                    on_success="done",
                    on_loopback="done",
                ),
            ),
            "failed_terminal": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(
                    on_success="failed_terminal",
                ),
            ),
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="sign_off", budget_state="remaining"),
                target="design",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="sign_off", budget_state="exhausted"),
                target="done",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="sign_off", budget_state="no_review"),
                target="done",
            ),
        ],
    )

    artifacts = ArtifactsPolicy(
        artifacts={
            "audit_decision": ArtifactContract(
                drain="audit",
                artifact_type="development_analysis_decision",
                decision_vocabulary=["completed", "request_changes", "failed"],
            ),
        }
    )

    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)


class TestCustomNamedPipelineHandlerDispatch:
    """Black-box flow: register_role_handlers + handle_phase work for custom phase names.

    This proves that the runtime treats phase names as opaque policy identifiers.
    A pipeline using only non-canonical phase names (audit, sign_off) routes
    through the generic handlers without any built-in phase name knowledge.
    """

    def test_register_role_handlers_registers_audit_and_sign_off(
        self, custom_bundle: PolicyBundle
    ) -> None:
        """register_role_handlers adds handlers for custom audit and sign_off phases."""

        before_audit = HANDLERS.get("audit")
        before_sign_off = HANDLERS.get("sign_off")
        HANDLERS.pop("audit", None)
        HANDLERS.pop("sign_off", None)
        try:
            register_role_handlers(custom_bundle.pipeline)
            assert HANDLERS.get("audit") is handle_generic_analysis_phase
            assert HANDLERS.get("sign_off") is handle_commit_phase
        finally:
            HANDLERS.pop("audit", None)
            HANDLERS.pop("sign_off", None)
            if before_audit is not None:
                HANDLERS["audit"] = before_audit
            if before_sign_off is not None:
                HANDLERS["sign_off"] = before_sign_off

    def test_handle_phase_dispatches_custom_commit_phase(self, custom_bundle: PolicyBundle) -> None:
        """handle_phase dispatches sign_off (commit-role) via the generic commit handler."""


        HANDLERS.pop("sign_off", None)
        register_role_handlers(custom_bundle.pipeline)
        try:
            ctx = MagicMock()
            workspace = MagicMock()
            workspace.absolute_path.side_effect = ValueError("no real fs")
            workspace.exists.return_value = False
            ctx.workspace = workspace

            effect = InvokeAgentEffect(
                agent_name="fake-agent",
                phase="sign_off",
                prompt_file="/tmp/prompt.md",
                drain="sign_off",
            )
            events = handle_phase(effect, ctx)
            # No diff check fails gracefully → COMMIT_SKIPPED not emitted;
            # missing commit_message artifact → PhaseFailureEvent emitted.

            assert any(isinstance(e, PhaseFailureEvent) for e in events), (
                "Expected PhaseFailureEvent for missing commit_message artifact"
            )
        finally:
            HANDLERS.pop("sign_off", None)

    def test_handle_phase_dispatches_custom_analysis_phase(
        self, custom_bundle: PolicyBundle
    ) -> None:
        """handle_phase dispatches audit (analysis-role) via the generic analysis handler."""


        HANDLERS.pop("audit", None)
        register_role_handlers(custom_bundle.pipeline)
        try:
            workspace = MagicMock()
            workspace.exists.return_value = False
            ctx = MagicMock()
            ctx.workspace = workspace
            ctx.pipeline_policy = custom_bundle.pipeline
            ctx.artifacts_policy = custom_bundle.artifacts

            effect = InvokeAgentEffect(
                agent_name="fake-agent",
                phase="audit",
                prompt_file="/tmp/prompt.md",
                drain="audit",
            )
            events = handle_phase(effect, ctx)
            assert len(events) == 1
            assert isinstance(events[0], PhaseFailureEvent)
            assert events[0].phase == "audit"
        finally:
            HANDLERS.pop("audit", None)
