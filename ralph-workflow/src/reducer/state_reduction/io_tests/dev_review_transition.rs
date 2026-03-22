// Dev->Review transition agent chain tests.
//
// Tests for agent chain handling when transitioning from Development to Review phase,
// including chain clearing, initialization, and auth failure handling.

use std::sync::Arc;

use super::*;
use crate::reducer::event::AgentErrorKind;

/// When transitioning from Development to Review (via `CommitCreated` or `CommitSkipped`),
/// the agent chain must be cleared so that orchestration will emit `InitializeAgentChain`
/// for the Reviewer role. This ensures the reviewer fallback chain is used.
#[test]
fn test_commit_created_clears_agent_chain_when_dev_to_review() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Development),
            iteration: 4,
            total_iterations: 5,
            total_reviewer_passes: 2,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["dev-agent-1".to_string(), "dev-agent-2".to_string()],
                    vec![vec![], vec![]],
                    AgentRole::Developer,
                )
                .with_max_cycles(3),
            ..base
        }
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "test commit".to_string()),
    );

    // Should transition to Review
    assert_eq!(new_state.phase, PipelinePhase::Review);
    // Agent chain should be cleared (empty agents list) so orchestration
    // will initialize it for Reviewer role
    assert!(
        new_state.agent_chain.agents.is_empty(),
        "Agent chain should be cleared when transitioning from Dev to Review, got agents: {:?}",
        new_state.agent_chain.agents
    );
    assert_eq!(
        new_state.agent_chain.current_role,
        AgentRole::Reviewer,
        "Agent chain role should be set to Reviewer"
    );
    assert_eq!(
        new_state.agent_chain.current_agent_index, 0,
        "Agent chain index should be reset to 0"
    );
}

/// Same test for `CommitSkipped` - should also clear agent chain for dev->review transition
#[test]
fn test_commit_skipped_clears_agent_chain_when_dev_to_review() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Development),
            iteration: 4,
            total_iterations: 5,
            total_reviewer_passes: 2,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["dev-agent-1".to_string()],
                    vec![vec![]],
                    AgentRole::Developer,
                )
                .with_max_cycles(3),
            ..base
        }
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("no changes".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert!(
        new_state.agent_chain.agents.is_empty(),
        "Agent chain should be cleared when transitioning from Dev to Review via skip"
    );
    assert_eq!(new_state.agent_chain.current_role, AgentRole::Reviewer);
    assert_eq!(
        new_state.agent_chain.current_drain,
        crate::agents::AgentDrain::Review,
        "Agent chain drain should be reset to Review"
    );
}

/// Verify that after `ChainInitialized` for Reviewer, the reducer correctly populates
/// `state.agent_chain` with the fallback agents in order.
#[test]
fn test_chain_initialized_populates_reviewer_chain() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            agent_chain: AgentChainState::initial().reset_for_role(AgentRole::Reviewer),
            ..base
        }
    };

    let new_state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec![
                AgentName::from("codex"),
                AgentName::from("opencode"),
                AgentName::from("claude"),
            ],
            3,
            1000,
            2.0,
            60000,
        ),
    );

    assert_eq!(
        new_state.agent_chain.agents,
        Arc::from(vec![
            "codex".to_string(),
            "opencode".to_string(),
            "claude".to_string()
        ]),
        "Reducer should store the exact fallback chain from ChainInitialized event"
    );
    assert_eq!(
        new_state.agent_chain.current_agent(),
        Some(&"codex".to_string()),
        "First agent in chain should be 'codex' (first fallback)"
    );
    assert_eq!(new_state.agent_chain.current_agent_index, 0);
    assert_eq!(new_state.agent_chain.current_role, AgentRole::Reviewer);
    assert_eq!(
        new_state.agent_chain.current_drain,
        crate::agents::AgentDrain::Review,
        "Agent chain drain should be reset to Review"
    );
}

/// Auth failure during review should advance the reducer's agent chain,
/// not just a local variable in review.rs
#[test]
fn test_auth_failure_during_review_advances_reducer_chain() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec![
                        "codex".to_string(),
                        "opencode".to_string(),
                        "claude".to_string(),
                    ],
                    vec![vec![], vec![], vec![]],
                    AgentRole::Reviewer,
                )
                .reset_for_role(AgentRole::Reviewer),
            ..base
        }
    };

    assert_eq!(
        state.agent_chain.current_agent(),
        Some(&"codex".to_string()),
        "Precondition: current agent should be codex"
    );

    let new_state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Reviewer,
            AgentName::from("codex"),
            1,
            AgentErrorKind::Authentication,
            false,
        ),
    );

    assert_eq!(
        new_state.agent_chain.current_agent(),
        Some(&"opencode".to_string()),
        "Auth failure should advance reducer's agent chain to opencode"
    );
    assert_eq!(new_state.agent_chain.current_agent_index, 1);
}

/// Orchestration should emit `InitializeAgentChain` when entering Review phase
/// with an empty agent chain.
#[test]
fn test_orchestration_emits_init_chain_for_reviewer_after_dev_review_transition() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            agent_chain: AgentChainState::initial().reset_for_role(AgentRole::Reviewer),
            ..base
        }
    };

    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::InitializeAgentChain {
                drain: crate::agents::AgentDrain::Review,
                ..
            }
        ),
        "Orchestration should emit InitializeAgentChain for Reviewer when chain is empty, got {effect:?}"
    );
}

/// Verify that the agent chain used in review comes from reducer state,
/// not from local construction.
#[test]
fn test_review_phase_agent_selection_uses_reducer_state() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec![
                        "codex".to_string(),
                        "opencode".to_string(),
                        "claude".to_string(),
                    ],
                    vec![vec![], vec![], vec![]],
                    AgentRole::Reviewer,
                )
                .reset_for_role(AgentRole::Reviewer),
            ..base
        }
    };

    assert_eq!(
        state.agent_chain.current_agent(),
        Some(&"codex".to_string()),
        "Current agent should be 'codex' from reducer state"
    );

    let state = PipelineState {
        agent_chain: state.agent_chain.switch_to_next_agent(),
        ..state
    };

    assert_eq!(
        state.agent_chain.current_agent(),
        Some(&"opencode".to_string()),
        "After advance, current agent should be 'opencode'"
    );

    let effect = determine_next_effect(&state);

    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::PrepareReviewContext { .. }
        ),
        "Should emit PrepareReviewContext when chain is already initialized, got {effect:?}"
    );
}

/// When transitioning from Review → `CommitMessage` → Review (between review passes),
/// the agent chain should be cleared or reset to Reviewer role so orchestration
/// uses the reviewer chain, not the commit chain.
#[test]
fn test_commit_created_after_review_fix_clears_or_resets_chain() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Review),
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["commit-agent-1".to_string()],
                    vec![vec![]],
                    AgentRole::Commit,
                )
                .with_max_cycles(3),
            ..base
        }
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "fix commit".to_string()),
    );

    // Should transition back to Review for next pass
    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.reviewer_pass, 1);

    // CRITICAL: Agent chain should be either empty OR have Reviewer role
    // so orchestration will use/initialize the reviewer chain
    if !new_state.agent_chain.agents.is_empty() {
        assert_eq!(
            new_state.agent_chain.current_role,
            AgentRole::Reviewer,
            "If chain is not empty after commit, role must be Reviewer, got {:?}",
            new_state.agent_chain.current_role
        );
    }
    // If empty, orchestration will initialize for Reviewer role (tested elsewhere)
}

/// Same test for `CommitSkipped`
#[test]
fn test_commit_skipped_after_review_fix_clears_or_resets_chain() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Review),
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["commit-agent-1".to_string()],
                    vec![vec![]],
                    AgentRole::Commit,
                )
                .with_max_cycles(3),
            ..base
        }
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("no changes".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.reviewer_pass, 1);

    if !new_state.agent_chain.agents.is_empty() {
        assert_eq!(
            new_state.agent_chain.current_role,
            AgentRole::Reviewer,
            "If chain is not empty after skip, role must be Reviewer"
        );
    }
}

/// When transitioning from Review back to Review (via `CommitCreated` after a fix),
/// the agent chain must be reset to ensure the Reviewer role chain is used.
/// This prevents the Commit agent chain from leaking into review passes.
#[test]
fn test_commit_created_resets_chain_when_review_to_review() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Review),
            reviewer_pass: 1,
            total_reviewer_passes: 3,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["commit-agent-1".to_string()],
                    vec![vec![]],
                    AgentRole::Commit,
                )
                .with_max_cycles(3),
            ..base
        }
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "fix commit".to_string()),
    );

    // Should transition back to Review for next pass
    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(
        new_state.reviewer_pass, 2,
        "Should increment to next review pass"
    );

    // Agent chain should be reset for Reviewer role
    assert!(
        new_state.agent_chain.agents.is_empty(),
        "Agent chain should be cleared when transitioning from Review back to Review, got agents: {:?}",
        new_state.agent_chain.agents
    );
    assert_eq!(
        new_state.agent_chain.current_role,
        AgentRole::Reviewer,
        "Agent chain role should be set to Reviewer"
    );
    assert_eq!(
        new_state.agent_chain.current_agent_index, 0,
        "Agent chain index should be reset to 0"
    );
}

/// Same test for `CommitSkipped` - should also reset chain for review->review transition
#[test]
fn test_commit_skipped_resets_chain_when_review_to_review() {
    let state = {
        let base = create_test_state();
        PipelineState {
            phase: PipelinePhase::CommitMessage,
            previous_phase: Some(PipelinePhase::Review),
            reviewer_pass: 1,
            total_reviewer_passes: 3,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["commit-agent-1".to_string()],
                    vec![vec![]],
                    AgentRole::Commit,
                )
                .with_max_cycles(3),
            ..base
        }
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("no changes".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::Review);
    assert_eq!(new_state.reviewer_pass, 2);
    assert!(
        new_state.agent_chain.agents.is_empty(),
        "Agent chain should be cleared when transitioning from Review to Review via skip"
    );
    assert_eq!(new_state.agent_chain.current_role, AgentRole::Reviewer);
}
