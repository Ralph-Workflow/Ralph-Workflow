from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ralph.agents.timeout_clock import FakeClock
from ralph.checked_accessors import as_int, as_mapping
from ralph.config.models import UnifiedConfig
from ralph.phases.execution import handle_execution_phase
from ralph.phases.phase_context import PhaseContext
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.checkpoint import load as load_checkpoint
from ralph.pipeline.checkpoint import save as save_checkpoint
from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.run_loop import build_recovery_controller
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory, FailureContext
from ralph.recovery.controller import RecoveryController
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from ralph.workspace.memory import MemoryWorkspace


def _make_failure(
    *,
    category: FailureCategory = FailureCategory.AGENT,
    reason: str = "transient error",
    agent: str = "claude",
    phase: str = "development",
    counts: bool = True,
) -> ClassifiedFailure:
    return ClassifiedFailure(
        category,
        reason,
        agent,
        phase,
        counts,
        None,
        reason,
    )


class TestRecoveryControllerDurability:
    def test_controller_snapshot_and_from_snapshot(self) -> None:
        clock = FakeClock(start=100.0)
        options = RecoveryControllerOptions(
            cycle_cap=50,
            backoff_attempts={"claude": 2},
            unavailable_timeouts={"development:claude": 120_000},
            clock=clock,
        )
        controller = RecoveryController(options=options)
        snap = controller.snapshot()

        assert snap["cycle_cap"] == 50
        backoff_attempts = {
            k: as_int(v, field=f"backoff_attempts.{k}")
            for k, v in as_mapping(snap["backoff_attempts"], field="backoff_attempts").items()
        }
        unavailable_timeouts = {
            k: as_int(v, field=f"unavailable_timeouts.{k}")
            for k, v in as_mapping(
                snap["unavailable_timeouts"], field="unavailable_timeouts"
            ).items()
        }
        assert backoff_attempts["claude"] == 2
        assert unavailable_timeouts["development:claude"] == 120_000

        restored_controller = RecoveryController.from_snapshot(snap, clock=clock)
        restored_snap = restored_controller.snapshot()
        restored_backoff = {
            k: as_int(v, field=f"backoff_attempts.{k}")
            for k, v in as_mapping(
                restored_snap["backoff_attempts"], field="backoff_attempts"
            ).items()
        }
        assert restored_snap["cycle_cap"] == 50
        assert restored_backoff["claude"] == 2

    def test_spent_agent_is_skipped_in_preferred_agent_selection(self) -> None:
        clock = FakeClock(start=100.0)
        options = RecoveryControllerOptions(
            clock=clock,
            agent_max_consecutive_unproductive=1,
            agent_max_total_unproductive=1,
        )
        controller = RecoveryController(options=options)
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude", "gemini"], current_index=0)
            },
        )

        class _AgentTimeoutError(Exception):
            pass

        _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

        controller.handle(
            state,
            _AgentTimeoutError("claude idle"),
            FailureContext(phase="development", agent="claude"),
        )

        selection = controller.preferred_agent_index("development", ["claude", "gemini"])
        assert selection.agent == "gemini"

    def test_prepopulated_workspace_no_change_demotes_at_consecutive_limit(self) -> None:
        bundle = load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")
        workspace = MemoryWorkspace()
        workspace.write("README.md", "# Project\nInitial readme.\n")
        workspace.write("src/app.py", "def main():\n    pass\n")

        clock = FakeClock(start=100.0)
        options = RecoveryControllerOptions(
            cycle_cap=50,
            policy_bundle=bundle,
            clock=clock,
            agent_max_consecutive_unproductive=3,
        )
        controller = RecoveryController(options=options)
        state = PipelineState.from_policy(
            bundle.pipeline,
            phase="development",
        )
        state = state.with_phase_chain(
            "development",
            AgentChainState(agents=["claude", "gemini"], current_index=0),
        )

        ctx = PhaseContext.construct(
            workspace=workspace,
            registry=MagicMock(),
            chain_manager=MagicMock(),
            pipeline_policy=bundle.pipeline,
            agents_policy=bundle.agents,
            artifacts_policy=bundle.artifacts,
        )

        invoke_effect = InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file="prompt.txt",
        )

        prep_events = handle_execution_phase(
            PreparePromptEffect(phase="development", iteration=1), ctx
        )
        assert prep_events == [PipelineEvent.PROMPT_PREPARED]

        exec_events = handle_execution_phase(invoke_effect, ctx)
        assert len(exec_events) == 1
        evt1 = exec_events[0]
        assert isinstance(evt1, PhaseFailureEvent)
        assert evt1.workspace_changed is False

        state, _ = reduce(state, evt1, bundle.pipeline, recovery=controller)
        assert controller.ledger.status("development", "claude").is_eligible is True
        assert controller.ledger.is_available("development", "claude") is True

        handle_execution_phase(PreparePromptEffect(phase="development", iteration=2), ctx)
        exec_events = handle_execution_phase(invoke_effect, ctx)
        assert len(exec_events) == 1
        evt2 = exec_events[0]
        assert isinstance(evt2, PhaseFailureEvent)
        assert evt2.workspace_changed is False
        state, _ = reduce(state, evt2, bundle.pipeline, recovery=controller)
        assert controller.ledger.status("development", "claude").is_eligible is True
        assert controller.ledger.is_available("development", "claude") is True

        handle_execution_phase(PreparePromptEffect(phase="development", iteration=3), ctx)
        exec_events = handle_execution_phase(invoke_effect, ctx)
        assert len(exec_events) == 1
        evt3 = exec_events[0]
        assert isinstance(evt3, PhaseFailureEvent)
        assert evt3.workspace_changed is False
        state, _ = reduce(state, evt3, bundle.pipeline, recovery=controller)
        status = controller.ledger.status("development", "claude")
        assert status.is_eligible is False
        assert status.is_cooldown is True
        assert controller.ledger.is_available("development", "claude") is False

    def test_prepopulated_workspace_with_file_modifications_detects_changes(self) -> None:
        bundle = load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")
        workspace = MemoryWorkspace()
        workspace.write("README.md", "# Project\nInitial readme.\n")
        workspace.write("src/app.py", "def main():\n    pass\n")

        clock = FakeClock(start=100.0)
        options = RecoveryControllerOptions(
            cycle_cap=50,
            policy_bundle=bundle,
            clock=clock,
            agent_max_consecutive_unproductive=3,
        )
        controller = RecoveryController(options=options)
        state = PipelineState.from_policy(
            bundle.pipeline,
            phase="development",
        )
        state = state.with_phase_chain(
            "development",
            AgentChainState(agents=["claude", "gemini"], current_index=0),
        )

        ctx = PhaseContext.construct(
            workspace=workspace,
            registry=MagicMock(),
            chain_manager=MagicMock(),
            pipeline_policy=bundle.pipeline,
            agents_policy=bundle.agents,
            artifacts_policy=bundle.artifacts,
        )

        invoke_effect = InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file="prompt.txt",
        )

        handle_execution_phase(PreparePromptEffect(phase="development", iteration=1), ctx)
        exec_events = handle_execution_phase(invoke_effect, ctx)
        assert len(exec_events) == 1
        evt1 = exec_events[0]
        assert isinstance(evt1, PhaseFailureEvent)
        assert evt1.workspace_changed is False
        state, _ = reduce(state, evt1, bundle.pipeline, recovery=controller)
        assert controller.ledger.status("development", "claude").is_eligible is True

        handle_execution_phase(PreparePromptEffect(phase="development", iteration=2), ctx)
        workspace.write("src/app.py", "def main():\n    print('modified')\n")
        exec_events = handle_execution_phase(invoke_effect, ctx)
        assert len(exec_events) == 1
        evt2 = exec_events[0]
        assert isinstance(evt2, PhaseFailureEvent)
        assert evt2.workspace_changed is True
        state, _ = reduce(state, evt2, bundle.pipeline, recovery=controller)
        assert controller.ledger.status("development", "claude").is_eligible is True
        assert controller.ledger.is_available("development", "claude") is True

    def test_checkpoint_resume_restores_ledger_snapshot_with_fresh_controller_and_clock(
        self, tmp_path: Path
    ) -> None:
        bundle = load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")
        config = UnifiedConfig()

        clock1 = FakeClock(start=100.0)
        options1 = RecoveryControllerOptions(
            cycle_cap=50,
            policy_bundle=bundle,
            clock=clock1,
            agent_max_consecutive_unproductive=3,
        )
        controller1 = RecoveryController(options=options1)

        state = PipelineState.from_policy(
            bundle.pipeline,
            phase="development",
        )
        state = state.with_phase_chain(
            "development",
            AgentChainState(agents=["claude", "gemini"], current_index=0),
        )

        fail_evt = PhaseFailureEvent(
            phase="development",
            reason="Validation failed",
            recoverable=True,
            workspace_changed=False,
        )
        state, _ = reduce(state, fail_evt, bundle.pipeline, recovery=controller1)
        state, _ = reduce(state, fail_evt, bundle.pipeline, recovery=controller1)

        assert state.ledger_snapshot is not None
        assert "development:claude" in as_mapping(state.ledger_snapshot["entries"])
        assert controller1.ledger.status("development", "claude").is_eligible is True

        checkpoint_path = tmp_path / "checkpoint.json"
        save_checkpoint(state, checkpoint_path)

        loaded_state = load_checkpoint(checkpoint_path)
        assert loaded_state is not None
        assert loaded_state.ledger_snapshot is not None
        assert "development:claude" in as_mapping(loaded_state.ledger_snapshot["entries"])

        fresh_clock = FakeClock(start=5000.0)
        fresh_controller, _ = build_recovery_controller(
            loaded_state,
            bundle,
            config,
            clock=fresh_clock,
        )

        assert fresh_controller.ledger.status("development", "claude").is_eligible is True
        assert fresh_controller.ledger.is_available("development", "claude") is True

        loaded_state, _ = reduce(loaded_state, fail_evt, bundle.pipeline, recovery=fresh_controller)
        status = fresh_controller.ledger.status("development", "claude")
        assert status.is_eligible is False
        assert status.is_cooldown is True
        assert fresh_controller.ledger.is_available("development", "claude") is False
