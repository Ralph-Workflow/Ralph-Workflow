"""AgentBudgetRegistry and RecoveryController durability tests.

Split from :mod:`tests.test_retry_budget_durability` so neither module
carries more than one top-level test class (repo-structure policy).
"""

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
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory
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
    """RecoveryController snapshot, restoration, and durability across restart."""

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

    def test_exhausted_agent_remains_spent_in_preferred_agent_selection(self) -> None:
        clock = FakeClock(start=100.0)
        budgets = {
            "development:claude": {
                "max_retries": 2,
                "consumed": 2,
                "remaining": 0,
                "exhausted": 1,
            }
        }
        options = RecoveryControllerOptions(
            budgets=budgets,
            clock=clock,
        )
        controller = RecoveryController(options=options)

        selection = controller.preferred_agent_index("development", ["claude", "gemini"])
        # claude's budget is exhausted, so claude is spent; gemini should be selected
        assert selection.agent == "gemini"

    def test_prepopulated_workspace_no_change_demotes_at_consecutive_limit(self) -> None:
        """A pre-populated workspace with existing files where an agent does not change
        any files correctly detects no workspace change and demotes at 3 consecutive attempts.
        """
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

        # Attempt 1: agent runs, does not change workspace files, fails artifact validation
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
        snap_entries = as_mapping(controller.ledger.snapshot()["entries"])
        claude_snap = as_mapping(snap_entries["development:claude"])
        assert as_int(claude_snap["consecutive_unproductive"]) == 1
        assert controller.ledger.status("development", "claude").is_eligible is True

        # Attempt 2: still no workspace change
        handle_execution_phase(PreparePromptEffect(phase="development", iteration=2), ctx)
        exec_events = handle_execution_phase(invoke_effect, ctx)
        assert len(exec_events) == 1
        evt2 = exec_events[0]
        assert isinstance(evt2, PhaseFailureEvent)
        assert evt2.workspace_changed is False
        state, _ = reduce(state, evt2, bundle.pipeline, recovery=controller)
        snap_entries = as_mapping(controller.ledger.snapshot()["entries"])
        claude_snap = as_mapping(snap_entries["development:claude"])
        assert as_int(claude_snap["consecutive_unproductive"]) == 2
        assert controller.ledger.status("development", "claude").is_eligible is True

        # Attempt 3: still no workspace change -> demotes at limit (3)
        handle_execution_phase(PreparePromptEffect(phase="development", iteration=3), ctx)
        exec_events = handle_execution_phase(invoke_effect, ctx)
        assert len(exec_events) == 1
        evt3 = exec_events[0]
        assert isinstance(evt3, PhaseFailureEvent)
        assert evt3.workspace_changed is False
        state, _ = reduce(state, evt3, bundle.pipeline, recovery=controller)
        snap_entries = as_mapping(controller.ledger.snapshot()["entries"])
        claude_snap = as_mapping(snap_entries["development:claude"])
        assert as_int(claude_snap["consecutive_unproductive"]) == 3
        status = controller.ledger.status("development", "claude")
        assert status.is_eligible is False
        assert status.is_cooldown is True

    def test_prepopulated_workspace_with_file_modifications_detects_changes(self) -> None:
        """When an agent modifies or adds files in a pre-populated workspace,
        workspace_changed is True and consecutive unproductive count is reset.
        """
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

        # Attempt 1: no changes, fails validation
        handle_execution_phase(PreparePromptEffect(phase="development", iteration=1), ctx)
        exec_events = handle_execution_phase(invoke_effect, ctx)
        assert len(exec_events) == 1
        evt1 = exec_events[0]
        assert isinstance(evt1, PhaseFailureEvent)
        assert evt1.workspace_changed is False
        state, _ = reduce(state, evt1, bundle.pipeline, recovery=controller)
        snap_entries = as_mapping(controller.ledger.snapshot()["entries"])
        claude_entry = as_mapping(snap_entries["development:claude"])
        assert as_int(claude_entry["consecutive_unproductive"]) == 1

        # Attempt 2: agent modifies src/app.py during execution, but artifact validation still fails
        handle_execution_phase(PreparePromptEffect(phase="development", iteration=2), ctx)
        workspace.write("src/app.py", "def main():\n    print('modified')\n")
        exec_events = handle_execution_phase(invoke_effect, ctx)
        assert len(exec_events) == 1
        evt2 = exec_events[0]
        assert isinstance(evt2, PhaseFailureEvent)
        assert evt2.workspace_changed is True
        state, _ = reduce(state, evt2, bundle.pipeline, recovery=controller)
        snap_entries = as_mapping(controller.ledger.snapshot()["entries"])
        entry = as_mapping(snap_entries["development:claude"])
        # consecutive reset because workspace changed (productive attempt)
        assert as_int(entry["consecutive_unproductive"]) == 0
        assert as_int(entry["total_unproductive"]) == 2
        assert controller.ledger.status("development", "claude").is_eligible is True

    def test_checkpoint_resume_restores_ledger_snapshot_with_fresh_controller_and_clock(
        self, tmp_path: Path
    ) -> None:
        """Resuming from checkpoint in a fresh controller with a fresh clock restores
        the ledger snapshot and preserves two prior failure counts.
        """
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
        state_entries = as_mapping(state.ledger_snapshot["entries"])
        claude_entry = as_mapping(state_entries["development:claude"])
        assert as_int(claude_entry["consecutive_unproductive"]) == 2
        assert as_int(claude_entry["total_unproductive"]) == 2

        # Save to checkpoint file
        checkpoint_path = tmp_path / "checkpoint.json"
        save_checkpoint(state, checkpoint_path)

        # Load from checkpoint file
        loaded_state = load_checkpoint(checkpoint_path)
        assert loaded_state is not None
        assert loaded_state.ledger_snapshot is not None
        loaded_entries = as_mapping(loaded_state.ledger_snapshot["entries"])
        loaded_claude = as_mapping(loaded_entries["development:claude"])
        assert as_int(loaded_claude["consecutive_unproductive"]) == 2

        # Fresh controller instance with a fresh clock (simulating process restart)
        fresh_clock = FakeClock(start=5000.0)
        fresh_controller, _ = build_recovery_controller(
            loaded_state,
            bundle,
            config,
            clock=fresh_clock,
        )

        # Verify ledger state was restored in the fresh controller
        fresh_entries = as_mapping(fresh_controller.ledger.snapshot()["entries"])
        fresh_snap = as_mapping(fresh_entries["development:claude"])
        assert as_int(fresh_snap["consecutive_unproductive"]) == 2
        assert as_int(fresh_snap["total_unproductive"]) == 2
        assert fresh_controller.ledger.status("development", "claude").is_eligible is True

        # Third failure on the fresh controller demotes claude
        loaded_state, _ = reduce(loaded_state, fail_evt, bundle.pipeline, recovery=fresh_controller)
        final_entries = as_mapping(fresh_controller.ledger.snapshot()["entries"])
        final_snap = as_mapping(final_entries["development:claude"])
        assert as_int(final_snap["consecutive_unproductive"]) == 3
        status = fresh_controller.ledger.status("development", "claude")
        assert status.is_eligible is False
        assert status.is_cooldown is True
