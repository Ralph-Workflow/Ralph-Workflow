//! Resume integration tests.
//!
//! Tests verify that resume functionality works correctly with reducer state machine.
//! Tests cover resume at all pipeline phases (planning, development, review, commit).
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::test_timeout::with_default_timeout;
use ralph_workflow::checkpoint::state::{AgentConfigSnapshot, CliArgsSnapshot, RebaseState};
use ralph_workflow::checkpoint::{
    CheckpointBuilder, PipelineCheckpoint, PipelinePhase as CheckpointPhase,
};
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::state::PipelineState;

fn create_minimal_agent_config(name: &str) -> AgentConfigSnapshot {
    AgentConfigSnapshot {
        name: name.to_string(),
        cmd: name.to_string(),
        output_flag: "-o".to_string(),
        yolo_flag: None,
        can_commit: true,
        model_override: None,
        provider_override: None,
        context_level: 1,
    }
}

const fn create_minimal_cli_args() -> CliArgsSnapshot {
    CliArgsSnapshot {
        developer_iters: 1,
        reviewer_reviews: 1,
        review_depth: None,
        isolation_mode: true,
        verbosity: 2,
        show_streaming_metrics: false,
        reviewer_json_parser: None,
    }
}

fn create_test_checkpoint(
    phase: CheckpointPhase,
    iteration: u32,
    total: u32,
    reviewer_pass: u32,
) -> PipelineCheckpoint {
    CheckpointBuilder::new()
        .phase(phase, iteration, total)
        .reviewer_pass(reviewer_pass, 2)
        .agents("claude", "claude")
        .cli_args(create_minimal_cli_args())
        .developer_config(create_minimal_agent_config("claude"))
        .reviewer_config(create_minimal_agent_config("claude"))
        .rebase_state(RebaseState::default())
        .git_identity(None, None)
        .build()
        .unwrap()
}

#[test]
fn test_pipeline_state_from_checkpoint_at_planning() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Planning, 0, 5, 0);
        let state = PipelineState::from(checkpoint);
        assert_eq!(state.phase, PipelinePhase::Planning);
        assert_eq!(state.iteration, 0);
    });
}

#[test]
fn test_pipeline_state_from_checkpoint_at_development() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 3, 5, 0);
        let state = PipelineState::from(checkpoint);
        assert_eq!(state.phase, PipelinePhase::Development);
        assert_eq!(state.iteration, 3);
    });
}

#[test]
fn test_pipeline_state_from_checkpoint_at_review() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Review, 5, 5, 1);
        let state = PipelineState::from(checkpoint);
        assert_eq!(state.phase, PipelinePhase::Review);
        assert_eq!(state.reviewer_pass, 1);
        assert_eq!(state.iteration, 5);
    });
}

#[test]
fn test_pipeline_state_from_checkpoint_at_commit() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::CommitMessage, 5, 5, 2);
        let state = PipelineState::from(checkpoint);
        assert_eq!(state.phase, PipelinePhase::CommitMessage);
        assert_eq!(state.reviewer_pass, 2);
        assert_eq!(state.iteration, 5);
    });
}

#[test]
fn test_pipeline_state_from_complete_checkpoint() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Complete, 5, 5, 2);
        let state = PipelineState::from(checkpoint);
        assert_eq!(state.phase, PipelinePhase::Complete);
    });
}

#[test]
fn test_resume_continues_from_correct_iteration() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 2, 5, 0);
        let state = PipelineState::from(checkpoint);
        assert_eq!(state.iteration, 2);
        assert_eq!(state.total_iterations, 5);
    });
}

#[test]
fn test_resume_continues_from_correct_reviewer_pass() {
    with_default_timeout(|| {
        let checkpoint = create_test_checkpoint(CheckpointPhase::Review, 5, 5, 1);
        let state = PipelineState::from(checkpoint);
        assert_eq!(state.reviewer_pass, 1);
        assert_eq!(state.total_reviewer_passes, 2);
    });
}

#[test]
fn test_agent_chain_starts_empty_after_resume() {
    with_default_timeout(|| {
        // Observable behavior: PipelineState::from(checkpoint) initializes
        // the agent chain via AgentChainState::initial(), which produces an
        // empty agent list. Agent chain configuration is supplied at runtime
        // from CLI args, not persisted in the checkpoint.
        let checkpoint = create_test_checkpoint(CheckpointPhase::Development, 2, 5, 0);
        let state = PipelineState::from(checkpoint);
        assert!(
            state.agent_chain.agents.is_empty(),
            "agent chain must be empty after resume from checkpoint; \
             agents are re-injected from CLI args at runtime"
        );
    });
}
