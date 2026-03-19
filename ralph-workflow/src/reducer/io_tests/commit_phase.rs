//! Tests for commit phase events (generation, validation, agent fallback).
//!
//! These tests validate the critical commit validation agent fallback fix
//! that prevents infinite loops when validation fails.

use std::sync::Arc;

use super::*;
use crate::agents::AgentRole;
use crate::reducer::event::CheckpointTrigger;
use crate::reducer::state::{ContinuationState, MAX_VALIDATION_RETRY_ATTEMPTS};

#[test]
fn test_commit_generation_started_sets_generating_state() {
    let state = create_test_state();
    let new_state = reduce(state.clone(), PipelineEvent::commit_generation_started());

    // Phase should be preserved
    assert_eq!(new_state.phase, state.phase);

    // Commit state should transition to Generating
    assert!(matches!(
        new_state.commit,
        CommitState::Generating {
            attempt: 1,
            max_attempts: MAX_VALIDATION_RETRY_ATTEMPTS
        }
    ));
}

#[test]
fn test_commit_prompt_prepared_starts_generation_when_not_started() {
    let state = PipelineState {
        commit: CommitState::NotStarted,
        ..create_test_state()
    };

    let new_state = reduce(state, PipelineEvent::commit_prompt_prepared(1));

    assert!(matches!(
        new_state.commit,
        CommitState::Generating {
            attempt: 1,
            max_attempts: MAX_VALIDATION_RETRY_ATTEMPTS
        }
    ));
}

#[test]
fn test_commit_message_generated_sets_commit_to_generated() {
    let state = create_test_state();
    let new_state = reduce(
        state,
        PipelineEvent::commit_message_generated("feat: add feature".to_string(), 1),
    );

    assert!(matches!(new_state.commit, CommitState::Generated { .. }));
}

#[test]
fn test_commit_message_generated_stores_message() {
    let state = create_test_state();
    let message = "fix: resolve bug".to_string();
    let new_state = reduce(
        state,
        PipelineEvent::commit_message_generated(message.clone(), 1),
    );

    if let CommitState::Generated {
        message: stored_msg,
    } = new_state.commit
    {
        assert_eq!(stored_msg, message);
    } else {
        panic!("Expected CommitState::Generated");
    }
}

#[test]
fn test_commit_created_sets_commit_to_committed() {
    let state = create_test_state();
    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "feat: test".to_string()),
    );

    assert!(matches!(new_state.commit, CommitState::Committed { .. }));
}

#[test]
fn test_commit_created_transitions_to_final_validation() {
    let state = create_state_in_phase(PipelinePhase::CommitMessage);
    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "feat: test".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::FinalValidation);
}

#[test]
fn test_commit_created_stores_hash() {
    let state = create_test_state();
    let hash = "abc123def456".to_string();
    let new_state = reduce(
        state,
        PipelineEvent::commit_created(hash.clone(), "test".to_string()),
    );

    if let CommitState::Committed { hash: stored_hash } = new_state.commit {
        assert_eq!(stored_hash, hash);
    } else {
        panic!("Expected CommitState::Committed");
    }
}

#[test]
fn test_commit_message_validation_failed_retries() {
    let state = PipelineState {
        commit: CommitState::Generated {
            message: "test".to_string(),
        },
        ..create_test_state()
    };
    let new_state = reduce(
        state,
        PipelineEvent::commit_message_validation_failed("Invalid format".to_string(), 1),
    );

    // Should retry with XSD retry pending, keeping attempt stable so attempt-scoped
    // materialized inputs can be reused.
    assert!(matches!(
        new_state.commit,
        CommitState::Generating { attempt: 1, .. }
    ));
    assert!(new_state.continuation.xsd_retry_pending);
}

#[test]
fn test_commit_message_validation_failed_exhausts_attempts_with_more_agents() {
    // Setup: 3 commit agents available
    let base_state = create_test_state();
    let state = PipelineState {
        continuation: ContinuationState {
            xsd_retry_count: 0,
            max_xsd_retry_count: 1,
            ..ContinuationState::new()
        },
        agent_chain: base_state.agent_chain.with_agents(
            vec![
                "commit-agent-1".to_string(),
                "commit-agent-2".to_string(),
                "commit-agent-3".to_string(),
            ],
            vec![vec![], vec![], vec![]],
            AgentRole::Commit,
        ),
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: MAX_VALIDATION_RETRY_ATTEMPTS,
        },
        ..base_state
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_message_validation_failed("Invalid format".to_string(), 1),
    );

    // With XSD retry budget exhausted, should advance to next agent and reset retry state.
    assert_eq!(new_state.agent_chain.current_agent_index, 1);
    assert!(matches!(
        new_state.commit,
        CommitState::Generating { attempt: 1, .. }
    ));
    assert_eq!(new_state.continuation.xsd_retry_count, 0);
    assert!(!new_state.continuation.xsd_retry_pending);
}

#[test]
fn test_commit_prompt_prepared_clears_xsd_retry_pending() {
    // Preparing a prompt starts a new attempt, so xsd_retry_pending should be cleared.
    let state = PipelineState {
        continuation: ContinuationState {
            xsd_retry_pending: true,
            ..ContinuationState::new()
        },
        ..create_state_in_phase(PipelinePhase::CommitMessage)
    };

    let new_state = reduce(state, PipelineEvent::commit_prompt_prepared(1));

    assert!(
        !new_state.continuation.xsd_retry_pending,
        "commit prompt preparation should clear xsd_retry_pending to prevent infinite retry loops"
    );
}

#[test]
fn test_commit_message_validation_failed_exhausts_all_agents() {
    // Setup: On last agent (index 2 of 3 agents)
    let base_state = create_test_state();
    let state = PipelineState {
        continuation: ContinuationState {
            xsd_retry_count: 0,
            max_xsd_retry_count: 1,
            ..ContinuationState::new()
        },
        agent_chain: base_state
            .agent_chain
            .with_agents(
                vec![
                    "commit-agent-1".to_string(),
                    "commit-agent-2".to_string(),
                    "commit-agent-3".to_string(),
                ],
                vec![vec![], vec![], vec![]],
                AgentRole::Commit,
            )
            .switch_to_next_agent()
            .switch_to_next_agent(), // Move to last agent (index 2)
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: MAX_VALIDATION_RETRY_ATTEMPTS,
        },
        ..base_state
    };

    // Verify we're on the last agent and retry_cycle is 0
    assert_eq!(state.agent_chain.current_agent_index, 2);
    assert_eq!(state.agent_chain.retry_cycle, 0);

    let new_state = reduce(
        state,
        PipelineEvent::commit_message_validation_failed("Invalid format".to_string(), 1),
    );

    // When we try to advance from last agent, switch_to_next_agent() wraps around:
    // - Index wraps back to 0
    // - Retry cycle increments to 1
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.retry_cycle, 1);

    // Since we wrapped around (exhausted all agents in this cycle), should give up
    assert!(matches!(new_state.commit, CommitState::NotStarted));
}

#[test]
fn test_commit_message_validation_failed_with_single_agent() {
    // Setup: Only 1 commit agent
    let base_state = create_test_state();
    let state = PipelineState {
        continuation: ContinuationState {
            xsd_retry_count: 0,
            max_xsd_retry_count: 1,
            ..ContinuationState::new()
        },
        agent_chain: base_state.agent_chain.with_agents(
            vec!["commit-agent-1".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: MAX_VALIDATION_RETRY_ATTEMPTS,
        },
        ..base_state
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_message_validation_failed("Invalid format".to_string(), 1),
    );

    // No more agents to fallback to - should give up
    assert!(matches!(new_state.commit, CommitState::NotStarted));
}

#[test]
fn test_commit_skipped_sets_commit_to_skipped() {
    let state = create_test_state();
    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("No changes".to_string()),
    );

    assert!(matches!(new_state.commit, CommitState::Skipped));
}

#[test]
fn test_commit_skipped_transitions_to_final_validation() {
    let state = create_state_in_phase(PipelinePhase::CommitMessage);
    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("No changes".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::FinalValidation);
}

#[test]
fn test_commit_generation_failed_resets_commit_to_not_started() {
    let state = PipelineState {
        commit: CommitState::Generating {
            attempt: 5,
            max_attempts: 10,
        },
        ..create_test_state()
    };
    let new_state = reduce(
        state,
        PipelineEvent::commit_generation_failed(
            "Agent failed to generate valid commit message".to_string(),
        ),
    );

    assert!(matches!(new_state.commit, CommitState::NotStarted));
}

#[test]
fn test_checkpoint_saved_preserves_all_state() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 3,
        reviewer_pass: 1,
        commit: CommitState::Generated {
            message: "test".to_string(),
        },
        ..create_test_state()
    };
    let new_state = reduce(
        state.clone(),
        PipelineEvent::checkpoint_saved(CheckpointTrigger::PhaseTransition),
    );

    assert_eq!(new_state.phase, state.phase);
    assert_eq!(new_state.iteration, state.iteration);
    assert_eq!(new_state.reviewer_pass, state.reviewer_pass);
    assert!(matches!(new_state.commit, CommitState::Generated { .. }));
}

#[test]
fn test_checkpoint_saved_with_different_triggers() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        iteration: 2,
        ..create_test_state()
    };

    // Test each checkpoint trigger type
    for trigger in [
        CheckpointTrigger::PhaseTransition,
        CheckpointTrigger::IterationComplete,
        CheckpointTrigger::BeforeRebase,
        CheckpointTrigger::Interrupt,
    ] {
        let new_state = reduce(state.clone(), PipelineEvent::checkpoint_saved(trigger));

        assert_eq!(new_state.phase, state.phase);
        assert_eq!(new_state.iteration, state.iteration);
    }
}

#[test]
fn test_commit_message_generated_increments_attempt() {
    let state = create_test_state();

    // Generate first message (attempt 1)
    let new_state = reduce(
        state,
        PipelineEvent::commit_message_generated("first".to_string(), 1),
    );

    assert!(matches!(new_state.commit, CommitState::Generated { .. }));
    if let CommitState::Generated { message } = &new_state.commit {
        assert_eq!(message, "first");
    }

    // Generate second message (attempt 2) - overwrites previous
    let new_state2 = reduce(
        new_state,
        PipelineEvent::commit_message_generated("second".to_string(), 2),
    );

    assert!(matches!(new_state2.commit, CommitState::Generated { .. }));
    if let CommitState::Generated { message } = &new_state2.commit {
        assert_eq!(message, "second");
    }
}

// ============================================================================
// CommitSkipped with previous_phase context tests
// ============================================================================
// These tests verify that CommitSkipped respects previous_phase for proper
// phase transitions, matching the behavior of CommitCreated.

#[test]
fn test_commit_skipped_returns_to_planning_after_development() {
    // When commit is skipped after development iteration,
    // should go back to Planning for next iteration (not FinalValidation)
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::Development),
        iteration: 0,
        total_iterations: 3,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("No changes to commit".to_string()),
    );

    // Should go to Planning for next iteration, not FinalValidation
    assert_eq!(new_state.phase, PipelinePhase::Planning);
    assert_eq!(new_state.iteration, 1); // Incremented
    assert!(new_state.previous_phase.is_none());
}

#[test]
fn test_commit_skipped_goes_to_review_after_last_development_iteration() {
    // When commit is skipped after last development iteration,
    // should go to Review (not FinalValidation)
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::Development),
        iteration: 2, // 0-indexed, this is the 3rd of 3 iterations
        total_iterations: 3,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("No changes to commit".to_string()),
    );

    // Should go to Review after all dev iterations done
    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.iteration, 3);
}

#[test]
fn test_commit_created_skips_review_when_no_reviewer_passes_configured() {
    // When the last development iteration is committed and reviewer passes are disabled,
    // the pipeline should skip Review entirely.
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::Development),
        iteration: 0,
        total_iterations: 1,
        total_reviewer_passes: 0,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "test".to_string()),
    );

    assert_eq!(
        new_state.phase,
        PipelinePhase::FinalValidation,
        "With total_reviewer_passes=0, pipeline should skip Review after last dev commit"
    );
}

#[test]
fn test_commit_skipped_returns_to_review_after_fix_attempt() {
    // When commit is skipped after fix attempt,
    // should stay in Review for next pass
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::Review),
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("No changes to commit".to_string()),
    );

    // Should go to Review for next pass
    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.reviewer_pass, 1); // Incremented
}

#[test]
fn test_commit_skipped_goes_to_final_validation_after_last_review() {
    // When commit is skipped after last review pass,
    // should go to FinalValidation
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: Some(PipelinePhase::Review),
        reviewer_pass: 1, // 0-indexed, this is the 2nd of 2 passes
        total_reviewer_passes: 2,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("No changes to commit".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::FinalValidation);
}

#[test]
fn test_commit_skipped_no_previous_phase_goes_to_final_validation() {
    // When commit is skipped with no previous phase context,
    // should go to FinalValidation (final commit scenario)
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        previous_phase: None,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("No changes to commit".to_string()),
    );

    // Should go to FinalValidation since no previous_phase indicates final commit
    assert_eq!(new_state.phase, PipelinePhase::FinalValidation);
}

// ============================================================================
// Commit Agent Chain Fallback Tests
// ============================================================================
// Tests verifying commit agent fallback to reviewer chain when no commit agents
// are configured. This is a critical behavior documented in agent-compatibility.md.

#[test]
fn test_commit_agent_chain_initialized_preserves_role() {
    // When commit agent chain is initialized, it should set the role to Commit
    let state = create_test_state();
    let agents = vec!["reviewer1".to_string()]; // Using reviewer as fallback

    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Commit,
            agents.clone(),
            3,
            1000,
            2.0,
            60000,
        ),
    );

    assert_eq!(new_state.agent_chain.agents, Arc::from(agents));
    assert_eq!(new_state.agent_chain.current_role, AgentRole::Commit);
}

#[test]
fn test_commit_agent_chain_fallback_works_with_reviewer_agents() {
    // When commit agent chain uses reviewer agents as fallback,
    // all fallback behavior should still work correctly
    let base_state = create_test_state();
    let state = PipelineState {
        continuation: ContinuationState {
            xsd_retry_count: 0,
            max_xsd_retry_count: 1,
            ..ContinuationState::new()
        },
        agent_chain: base_state.agent_chain.with_agents(
            vec!["reviewer1".to_string(), "reviewer2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Commit, // Using Commit role with reviewer agents
        ),
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: MAX_VALIDATION_RETRY_ATTEMPTS,
        },
        ..base_state
    };

    // Verify initial state
    assert_eq!(state.agent_chain.current_agent_index, 0);
    assert_eq!(state.agent_chain.current_role, AgentRole::Commit);

    // Exhaust first agent, should switch to second
    let new_state = reduce(
        state,
        PipelineEvent::commit_message_validation_failed("Invalid format".to_string(), 1),
    );

    assert_eq!(new_state.agent_chain.current_agent_index, 1);
    assert!(matches!(
        new_state.commit,
        CommitState::Generating { attempt: 1, .. }
    ));
}

#[test]
fn test_commit_agent_chain_empty_gives_up_immediately() {
    // When no commit agents are configured and handler returns empty chain,
    // the commit should transition to NotStarted
    let base_state = create_test_state();
    let state = PipelineState {
        continuation: ContinuationState {
            xsd_retry_count: 0,
            max_xsd_retry_count: 1,
            ..ContinuationState::new()
        },
        agent_chain: base_state.agent_chain.with_agents(
            vec![], // Empty agent chain
            vec![],
            AgentRole::Commit,
        ),
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: MAX_VALIDATION_RETRY_ATTEMPTS,
        },
        ..base_state
    };

    // With empty chain, validation failure should give up
    let new_state = reduce(
        state,
        PipelineEvent::commit_message_validation_failed("Invalid format".to_string(), 1),
    );

    // Should give up since no agents available
    assert!(matches!(new_state.commit, CommitState::NotStarted));
}

#[test]
fn test_commit_agent_role_preserved_across_retries() {
    // Verify that the agent chain role stays as Commit even when using reviewer agents
    let base_state = create_test_state();
    let mut state = PipelineState {
        agent_chain: base_state.agent_chain.with_agents(
            vec!["reviewer1".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: MAX_VALIDATION_RETRY_ATTEMPTS,
        },
        ..base_state
    };

    // Simulate multiple validation failures
    for i in 1..MAX_VALIDATION_RETRY_ATTEMPTS {
        state = reduce(
            state,
            PipelineEvent::commit_message_validation_failed("Invalid".to_string(), i),
        );
        assert_eq!(state.agent_chain.current_role, AgentRole::Commit);
    }
}
