//! Integration tests for development continuation budget exhaustion and agent fallback.
//!
//! These tests verify that when a development iteration's continuation budget is
//! exhausted (due to repeated failed/partial status), the pipeline switches to the
//! next agent in the fallback chain rather than immediately aborting.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../../INTEGRATION_TESTS.md](../../../INTEGRATION_TESTS.md)**.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{ErrorEvent, PipelineEvent, PipelinePhase, PromptInputEvent};
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::{AgentChainState, DevelopmentStatus, PipelineState};
use ralph_workflow::reducer::state_reduction::reduce;

#[test]
fn test_continuation_exhaustion_triggers_agent_fallback() {
    with_default_timeout(|| {
        // Given: A pipeline with 2 agents in the fallback chain
        let agent_chain = AgentChainState::initial().with_agents(
            vec!["agent-primary".to_string(), "agent-fallback".to_string()],
            vec![vec!["model-1".to_string()], vec!["model-2".to_string()]],
            ralph_workflow::agents::AgentRole::Developer,
        );

        let state = with_locked_prompt_permissions(PipelineState::initial(5, 3));
        let state = PipelineState {
            agent_chain,
            ..state
        };

        // When: Continuation budget is exhausted for the primary agent
        let new_state = reduce(
            state,
            PipelineEvent::development_continuation_budget_exhausted(
                0,
                3,
                DevelopmentStatus::Failed,
            ),
        );

        // Commit-gated design: After continuation budget exhaustion with agents remaining,
        // the iteration completes and transitions to CommitMessage (development_commit checkpoint)
        // before routing to Review (if development budget exhausted) or back to Planning.
        //
        // The behavior: budget exhaustion completes the iteration and routes to CommitMessage.
        assert_eq!(
            new_state.agent_chain.current_agent_index, 0,
            "Agent chain should be reset after iteration completion"
        );
        assert_eq!(
            new_state.phase,
            ralph_workflow::reducer::event::PipelinePhase::CommitMessage,
            "Should complete iteration and transition to CommitMessage after budget exhaustion (commit-gated design)"
        );

        // And: The continuation state should be reset
        assert_eq!(
            new_state.continuation.continuation_attempt, 0,
            "Continuation attempt should be reset after iteration completion"
        );

        // And: The orchestration should clean up continuation context before preparing context
        let effect = determine_next_effect(&new_state);
        assert!(
            matches!(effect, Effect::CleanupContinuationContext),
            "Should clean up continuation context before preparing for new agent; got {effect:?}"
        );
    });
}

#[test]
fn test_all_agents_exhausted_reports_chain_exhaustion() {
    with_default_timeout(|| {
        // Given: A pipeline with 2 agents, max 1 retry cycle
        let agent_chain = AgentChainState::initial()
            .with_agents(
                vec!["agent-a".to_string(), "agent-b".to_string()],
                vec![vec!["model-1".to_string()], vec!["model-2".to_string()]],
                ralph_workflow::agents::AgentRole::Developer,
            )
            .with_max_cycles(1);

        let mut state = with_locked_prompt_permissions(PipelineState::initial(5, 3));
        state.agent_chain = agent_chain;
        state.phase = ralph_workflow::reducer::event::PipelinePhase::Development;

        // UPDATED (wt-39 fix): After continuation budget exhaustion, the system now completes
        // the iteration instead of trying more agents. This test originally verified that
        // after trying all agents, the system would transition to AwaitingDevFix. With the
        // fix, the FIRST budget exhaustion completes the iteration, so we never get to the
        // "all agents exhausted" state within a single iteration.
        //
        // Commit-gated design: iteration completes through CommitMessage (development_commit
        // checkpoint) before routing to Review or back to Planning.
        state = reduce(
            state,
            PipelineEvent::development_continuation_budget_exhausted(
                0,
                3,
                DevelopmentStatus::Failed,
            ),
        );

        // Commit-gated design: iteration completes and transitions to CommitMessage
        assert_eq!(
            state.phase,
            PipelinePhase::CommitMessage,
            "Should complete iteration and transition to CommitMessage after budget exhaustion (commit-gated design)"
        );
        assert_eq!(
            state.agent_chain.current_agent_index, 0,
            "Agent chain should be reset after iteration completion"
        );

        // Orchestration should proceed with commit flow (not dev-fix in this scenario)
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::CheckCommitDiff)
                || matches!(effect, Effect::CleanupContinuationContext),
            "Should proceed with commit flow after iteration completion; got {effect:?}"
        );
    });
}

#[test]
fn test_agent_chain_exhausted_emits_completion_marker() {
    with_default_timeout(|| {
        // Given: A state where agent chain is exhausted
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Development;

        // Create an exhausted agent chain by setting retry_cycle to max_cycles
        let agent_chain = state
            .agent_chain
            .with_agents(
                vec!["agent1".to_string()],
                vec![vec![]],
                ralph_workflow::agents::AgentRole::Developer,
            )
            .with_max_cycles(1);

        // Set retry_cycle to max_cycles to make it exhausted
        state.agent_chain = AgentChainState {
            retry_cycle: 1,
            ..agent_chain
        };

        // Verify it's exhausted
        assert!(
            state.agent_chain.is_exhausted(),
            "Agent chain should be exhausted"
        );

        // When: The orchestration determines the next effect
        let effect = determine_next_effect(&state);

        // Then: It should report agent chain exhaustion
        assert!(
            matches!(effect, Effect::ReportAgentChainExhausted { .. }),
            "Expected ReportAgentChainExhausted, got {effect:?}"
        );

        // When: The error event is reduced
        let error_event = ErrorEvent::AgentChainExhausted {
            role: ralph_workflow::agents::AgentRole::Developer,
            phase: PipelinePhase::Development,
            cycle: 1,
        };
        let new_state = reduce(
            state,
            PipelineEvent::PromptInput(PromptInputEvent::HandlerError {
                phase: PipelinePhase::Development,
                error: error_event,
            }),
        );

        // Then: State transitions to AwaitingDevFix (not directly to Interrupted)
        assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
        assert_eq!(new_state.previous_phase, Some(PipelinePhase::Development));
        assert!(
            !new_state.is_complete(),
            "Should not be complete yet (still in AwaitingDevFix)"
        );

        // When: Orchestration determines next effect for AwaitingDevFix phase
        let next_effect = determine_next_effect(&new_state);

        // Then: It should trigger dev-fix flow
        assert!(
            matches!(next_effect, Effect::TriggerDevFixFlow { .. }),
            "Expected TriggerDevFixFlow for AwaitingDevFix phase, got {next_effect:?}"
        );

        // When: Dev-fix flow is triggered and completion marker is emitted
        let after_trigger_state = reduce(
            new_state,
            PipelineEvent::AwaitingDevFix(
                ralph_workflow::reducer::event::AwaitingDevFixEvent::DevFixTriggered {
                    failed_phase: PipelinePhase::Development,
                    failed_role: AgentRole::Developer,
                },
            ),
        );
        assert_eq!(after_trigger_state.phase, PipelinePhase::AwaitingDevFix);

        let after_fix_state = reduce(
            after_trigger_state,
            PipelineEvent::AwaitingDevFix(
                ralph_workflow::reducer::event::AwaitingDevFixEvent::DevFixCompleted {
                    success: false,
                    summary: None,
                },
            ),
        );

        let mut interrupted_state = reduce(
            after_fix_state,
            PipelineEvent::AwaitingDevFix(
                ralph_workflow::reducer::event::AwaitingDevFixEvent::CompletionMarkerEmitted {
                    is_failure: true,
                },
            ),
        );

        // Then: State transitions to Interrupted after completion marker
        assert_eq!(interrupted_state.phase, PipelinePhase::Interrupted);
        assert_eq!(
            interrupted_state.previous_phase,
            Some(PipelinePhase::AwaitingDevFix)
        );

        // When: Orchestration determines next effect for Interrupted phase
        interrupted_state.prompt_permissions.restored = true;

        let final_effect = determine_next_effect(&interrupted_state);

        // Then: It must run the pre-termination safety check before checkpointing.
        assert!(
            matches!(
                final_effect,
                Effect::CheckUncommittedChangesBeforeTermination
            ),
            "Expected CheckUncommittedChangesBeforeTermination for Interrupted phase, got {final_effect:?}"
        );

        // When: Safety check passes (simulate handler emitting event)
        let after_check_state = reduce(
            interrupted_state,
            PipelineEvent::pre_termination_safety_check_passed(),
        );

        let final_effect = determine_next_effect(&after_check_state);
        assert!(
            matches!(final_effect, Effect::SaveCheckpoint { .. }),
            "Expected SaveCheckpoint after safety check, got {final_effect:?}"
        );

        // When: Checkpoint is saved (simulate by applying CheckpointSaved event)
        let final_state = reduce(
            after_check_state,
            PipelineEvent::checkpoint_saved(
                ralph_workflow::reducer::CheckpointTrigger::PhaseTransition,
            ),
        );

        // Then: Pipeline is marked as complete
        assert!(
            final_state.is_complete(),
            "Pipeline should be complete after checkpoint saved in Interrupted phase"
        );
    });
}

#[test]
fn test_budget_exhausted_with_failed_status_transitions_to_awaiting_dev_fix() {
    with_default_timeout(|| {
        // Given: Pipeline in Development Iteration 2 with exhausted continuation budget
        // AND all agents exhausted AND last status is Failed
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 0));
        state.phase = PipelinePhase::Development;
        state.iteration = 2;

        // Create a single-agent chain (will be exhausted after first exhaustion)
        let agent_chain = AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_max_cycles(1);
        state.agent_chain = agent_chain;

        // When: Continuation budget exhausted with Status: Failed
        let new_state = reduce(
            state,
            PipelineEvent::development_continuation_budget_exhausted(
                2,
                3,
                DevelopmentStatus::Failed,
            ),
        );

        // Then: Should transition to AwaitingDevFix (not stay in Development)
        assert_eq!(
            new_state.phase,
            PipelinePhase::AwaitingDevFix,
            "Should transition to AwaitingDevFix when budget exhausted with Failed status and all agents exhausted"
        );
        assert_eq!(
            new_state.previous_phase,
            Some(PipelinePhase::Development),
            "Should preserve previous phase for completion marker logic"
        );
        assert!(
            !new_state.dev_fix_triggered,
            "dev_fix_triggered should be false so TriggerDevFixFlow executes"
        );

        // And: TriggerDevFixFlow effect should be determined
        let effect = determine_next_effect(&new_state);
        assert!(
            matches!(effect, Effect::TriggerDevFixFlow { .. }),
            "Expected TriggerDevFixFlow, got {effect:?}"
        );
    });
}

#[test]
fn test_budget_exhausted_with_completed_status_proceeds_to_commit() {
    with_default_timeout(|| {
        // Given: Pipeline in Development with exhausted continuation budget
        // BUT last status is Completed
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 0));
        state.phase = PipelinePhase::Development;
        state.iteration = 1;

        // Create a single-agent chain (will be exhausted after first exhaustion)
        let agent_chain = AgentChainState::initial()
            .with_agents(
                vec!["agent1".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_max_cycles(1);
        state.agent_chain = agent_chain;

        // When: Continuation budget exhausted but Status: Completed
        // (This shouldn't happen in practice, but verifies the logic)
        let new_state = reduce(
            state,
            PipelineEvent::development_continuation_budget_exhausted(
                1,
                3,
                DevelopmentStatus::Completed,
            ),
        );

        // Commit-gated design: After continuation budget exhaustion, the iteration completes
        // and routes through CommitMessage (development_commit checkpoint) before proceeding.
        // This applies regardless of status: budget exhaustion always completes the iteration
        // and uses the commit-gated path: Development → CommitMessage → (Review or Planning).
        //
        // Note: In practice, Completed status would trigger ContinuationSucceeded or
        // IterationCompleted before reaching budget exhaustion, so this scenario is theoretical.
        assert_eq!(
            new_state.phase,
            PipelinePhase::CommitMessage,
            "Should complete iteration and transition to CommitMessage after budget exhaustion (commit-gated design)"
        );
    });
}
