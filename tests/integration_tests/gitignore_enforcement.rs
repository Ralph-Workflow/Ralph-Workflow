//! Gitignore enforcement integration tests.
//!
//! Tests for the automatic .gitignore entry enforcement feature that ensures
//! `/PROMPT*` and `.agent/` are present in .gitignore at pipeline start.
//!
//! These tests verify the orchestration behavior: that the effect runs at the
//! correct point in the pipeline lifecycle (after agent chain initialization,
//! before context cleanup in the Planning phase), and that it doesn't run
//! again on resume.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::{AgentChainState, PipelineState};

/// Test that gitignore effect runs before cleanup in Planning phase.
///
/// This verifies that when the pipeline starts in Planning phase and the agent
/// chain is initialized, the next effect is `EnsureGitignoreEntries` (before
/// `CleanupContext`).
#[test]
fn test_gitignore_ensured_before_cleanup() {
    with_default_timeout(|| {
        // Start with fresh state in Planning phase, agent chain initialized
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            gitignore_entries_ensured: false,
            context_cleaned: false,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["test-agent".to_string()],
                    vec![vec!["test-model".to_string()]],
                    AgentRole::Developer,
                )
                .with_drain(ralph_workflow::agents::AgentDrain::Planning),
            ..with_locked_prompt_permissions(PipelineState::initial(1, 1))
        };

        // Orchestrator should derive EnsureGitignoreEntries before CleanupContext
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::EnsureGitignoreEntries),
            "Expected EnsureGitignoreEntries, got {effect:?}"
        );
    });
}

/// Test that after gitignore is ensured, orchestration proceeds to cleanup.
///
/// This verifies that when `gitignore_entries_ensured` flag is set, the
/// orchestrator proceeds to the next effect (`CleanupContext`).
#[test]
fn test_gitignore_ensured_proceeds_to_cleanup() {
    with_default_timeout(|| {
        // State after gitignore ensured
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            gitignore_entries_ensured: true,
            context_cleaned: false,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["test-agent".to_string()],
                    vec![vec!["test-model".to_string()]],
                    AgentRole::Developer,
                )
                .with_drain(ralph_workflow::agents::AgentDrain::Planning),
            ..with_locked_prompt_permissions(PipelineState::initial(1, 1))
        };

        // Should proceed to CleanupContext
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::CleanupContext),
            "Expected CleanupContext after gitignore ensured, got {effect:?}"
        );
    });
}

/// Test that gitignore effect doesn't run again on resume.
///
/// This verifies that when resuming a pipeline where gitignore has already
/// been ensured, the orchestrator skips `EnsureGitignoreEntries` and proceeds
/// to the next effect.
#[test]
fn test_gitignore_not_rerun_on_resume() {
    with_default_timeout(|| {
        // Resume scenario: gitignore already ensured, context not cleaned
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            gitignore_entries_ensured: true,
            context_cleaned: false,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["test-agent".to_string()],
                    vec![vec!["test-model".to_string()]],
                    AgentRole::Developer,
                )
                .with_drain(ralph_workflow::agents::AgentDrain::Planning),
            ..with_locked_prompt_permissions(PipelineState::initial(1, 1))
        };

        // Should skip to next effect (cleanup)
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::CleanupContext),
            "Expected to skip gitignore check on resume, got {effect:?}"
        );
    });
}

/// Test that gitignore effect runs after agent chain initialization.
///
/// This verifies that the gitignore effect doesn't run before the agent chain
/// is initialized. Agent chain initialization must complete first.
#[test]
fn test_gitignore_after_agent_chain_init() {
    with_default_timeout(|| {
        // State without agent chain initialized
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            gitignore_entries_ensured: false,
            context_cleaned: false,
            agent_chain: AgentChainState::initial(), // Empty chain
            ..with_locked_prompt_permissions(PipelineState::initial(1, 1))
        };

        // Should initialize agent chain first
        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    role: AgentRole::Developer,
                    ..
                }
            ),
            "Expected InitializeAgentChain before gitignore, got {effect:?}"
        );
    });
}
