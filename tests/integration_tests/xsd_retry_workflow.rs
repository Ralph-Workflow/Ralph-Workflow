//! Integration tests for XSD retry workflows.
//!
//! These tests verify the end-to-end XSD retry behavior including:
//! - XSD retry triggers agent re-invocation with the same session
//! - XSD exhaustion triggers agent fallback with session clear
//! - Agent fallback resets all orchestration state for full rollback
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::{
    AgentChainState, CommitState, ContinuationState, PipelineState,
};
use ralph_workflow::reducer::state_reduction::reduce;

// ============================================================================
// PLANNING PHASE XSD RETRY TESTS
// ============================================================================

/// Test that planning XSD validation failure triggers agent re-invocation.
///
/// When planning XML validation fails and retries are available:
/// 1. Orchestration flags are reset (`prompt_prepared`, `agent_invoked`)
/// 2. Next effect should be to prepare the XSD retry prompt
/// 3. Session reuse is enabled for the retry
#[test]
fn test_planning_xsd_retry_triggers_reinvocation() {
    with_default_timeout(|| {
        // Setup: Agent has been invoked, but XML validation failed
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            iteration: 0,
            planning_prompt_prepared_iteration: Some(0),
            planning_required_files_cleaned_iteration: Some(0),
            planning_agent_invoked_iteration: Some(0),
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["claude".to_string()],
                    vec![vec![]],
                    AgentRole::Developer,
                )
                .with_session_id(Some("session-123".to_string())),
            context_cleaned: true,
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        // Event: XSD validation failed
        let new_state = reduce(
            state,
            PipelineEvent::planning_output_validation_failed(0, 0),
        );

        // Verify: Orchestration flags are reset for re-invocation
        assert!(
            new_state.planning_prompt_prepared_iteration.is_none(),
            "planning_prompt_prepared_iteration should be reset for XSD retry"
        );
        assert!(
            new_state.planning_agent_invoked_iteration.is_none(),
            "planning_agent_invoked_iteration should be reset for XSD retry"
        );

        // Verify: Session reuse is enabled
        assert!(
            new_state.continuation.xsd_retry_session_reuse_pending,
            "XSD retry should enable session reuse"
        );
        assert_eq!(
            new_state.agent_chain.last_session_id,
            Some("session-123".to_string()),
            "Session ID should be preserved for reuse"
        );

        // Verify: Next effect is to prepare the XSD retry prompt
        let effect = determine_next_effect(&new_state);
        assert!(
            matches!(effect, Effect::PreparePlanningPrompt { .. }),
            "Next effect should be PreparePlanningPrompt for XSD retry, got {effect:?}"
        );
    });
}

/// Test that planning XSD exhaustion triggers agent fallback with session clear.
///
/// When XSD retries are exhausted:
/// 1. Agent chain advances to next agent
/// 2. Session ID is cleared (new agent, new session)
/// 3. All orchestration flags are reset for full rollback
#[test]
fn test_planning_xsd_exhausted_triggers_fallback() {
    with_default_timeout(|| {
        // Setup: XSD retries almost exhausted
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            iteration: 0,
            planning_prompt_prepared_iteration: Some(0),
            planning_required_files_cleaned_iteration: Some(0),
            planning_agent_invoked_iteration: Some(0),
            continuation: ContinuationState {
                xsd_retry_count: 9,
                max_xsd_retry_count: 10,
                ..ContinuationState::new()
            },
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["agent1".to_string(), "agent2".to_string()],
                    vec![vec![], vec![]],
                    AgentRole::Developer,
                )
                .with_session_id(Some("session-123".to_string())),
            context_cleaned: true,
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        // Event: XSD validation failed (exhausts retries)
        let new_state = reduce(
            state,
            PipelineEvent::planning_output_validation_failed(0, 0),
        );

        // Verify: Agent chain advanced
        assert_eq!(
            new_state.agent_chain.current_agent_index, 1,
            "Should switch to next agent"
        );
        assert_eq!(
            new_state.agent_chain.current_agent().unwrap(),
            "agent2",
            "Current agent should be agent2"
        );

        // Verify: Session ID is cleared
        assert!(
            new_state.agent_chain.last_session_id.is_none(),
            "Session ID should be cleared when switching agents"
        );

        // Verify: Session reuse is NOT enabled
        assert!(
            !new_state.continuation.xsd_retry_session_reuse_pending,
            "Session reuse should be cleared after agent switch"
        );

        // Verify: XSD retry count is reset
        assert_eq!(
            new_state.continuation.xsd_retry_count, 0,
            "XSD retry count should be reset for new agent"
        );

        // Verify: All orchestration flags are reset
        assert!(
            new_state.planning_prompt_prepared_iteration.is_none(),
            "planning_prompt_prepared_iteration should be reset"
        );
        assert!(
            new_state.planning_agent_invoked_iteration.is_none(),
            "planning_agent_invoked_iteration should be reset"
        );
    });
}

// ============================================================================
// REVIEW PHASE XSD RETRY TESTS
// ============================================================================

/// Test that review XSD validation failure triggers agent re-invocation.
#[test]
fn test_review_xsd_retry_triggers_reinvocation() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            review_context_prepared_pass: Some(0),
            review_prompt_prepared_pass: Some(0),
            review_required_files_cleaned_pass: Some(0),
            review_agent_invoked_pass: Some(0),
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["reviewer".to_string()],
                    vec![vec![]],
                    AgentRole::Reviewer,
                )
                .with_session_id(Some("review-session".to_string())),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::review_output_validation_failed(0, 0, None),
        );

        // Verify: Orchestration flags reset for re-invocation
        assert!(
            new_state.review_prompt_prepared_pass.is_none(),
            "review_prompt_prepared_pass should be reset"
        );
        assert!(
            new_state.review_agent_invoked_pass.is_none(),
            "review_agent_invoked_pass should be reset"
        );

        // Verify: Session reuse enabled
        assert!(
            new_state.continuation.xsd_retry_session_reuse_pending,
            "Session reuse should be enabled"
        );
        assert_eq!(
            new_state.agent_chain.last_session_id,
            Some("review-session".to_string()),
            "Session ID should be preserved"
        );

        // Verify: Next effect prepares retry prompt
        let effect = determine_next_effect(&new_state);
        assert!(
            matches!(effect, Effect::PrepareReviewPrompt { .. }),
            "Should prepare review XSD retry prompt, got {effect:?}"
        );
    });
}

/// Test that review XSD exhaustion triggers agent fallback.
#[test]
fn test_review_xsd_exhausted_triggers_fallback() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            review_prompt_prepared_pass: Some(0),
            review_agent_invoked_pass: Some(0),
            continuation: ContinuationState {
                xsd_retry_count: 9,
                max_xsd_retry_count: 10,
                ..ContinuationState::new()
            },
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["reviewer1".to_string(), "reviewer2".to_string()],
                    vec![vec![], vec![]],
                    AgentRole::Reviewer,
                )
                .with_session_id(Some("session".to_string())),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::review_output_validation_failed(0, 0, None),
        );

        // Verify: Agent switched
        assert_eq!(new_state.agent_chain.current_agent_index, 1);

        // Verify: Session cleared
        assert!(new_state.agent_chain.last_session_id.is_none());

        // Verify: Orchestration reset
        assert!(new_state.review_prompt_prepared_pass.is_none());
        assert!(new_state.review_agent_invoked_pass.is_none());
    });
}

// ============================================================================
// FIX PHASE XSD RETRY TESTS
// ============================================================================

/// Test that fix XSD validation failure triggers agent re-invocation.
#[test]
fn test_fix_xsd_retry_triggers_reinvocation() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            review_issues_found: true, // In fix mode
            fix_prompt_prepared_pass: Some(0),
            fix_required_files_cleaned_pass: Some(0),
            fix_agent_invoked_pass: Some(0),
            agent_chain: AgentChainState::initial()
                .with_agents(vec!["fixer".to_string()], vec![vec![]], AgentRole::Reviewer)
                .with_session_id(Some("fix-session".to_string())),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::fix_output_validation_failed(0, 0, None),
        );

        // Verify: Orchestration flags reset
        assert!(new_state.fix_prompt_prepared_pass.is_none());
        assert!(new_state.fix_agent_invoked_pass.is_none());

        // Verify: Session reuse enabled
        assert!(new_state.continuation.xsd_retry_session_reuse_pending);
        assert_eq!(
            new_state.agent_chain.last_session_id,
            Some("fix-session".to_string())
        );

        // Verify: Next effect prepares fix retry prompt
        let effect = determine_next_effect(&new_state);
        assert!(
            matches!(effect, Effect::PrepareFixPrompt { .. }),
            "Should prepare fix XSD retry prompt, got {effect:?}"
        );
    });
}

/// Test that fix XSD retry stays on the explicit fix drain even when review flags are clear.
#[test]
fn test_fix_xsd_retry_uses_fix_drain_without_review_issue_flag() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            review_issues_found: false,
            fix_prompt_prepared_pass: Some(0),
            fix_required_files_cleaned_pass: Some(0),
            fix_agent_invoked_pass: Some(0),
            agent_chain: AgentChainState::initial()
                .with_agents(vec!["fixer".to_string()], vec![vec![]], AgentRole::Reviewer)
                .with_drain(ralph_workflow::agents::AgentDrain::Fix)
                .with_session_id(Some("fix-session".to_string())),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::fix_output_validation_failed(0, 0, None),
        );

        assert!(new_state.fix_prompt_prepared_pass.is_none());
        assert!(new_state.fix_agent_invoked_pass.is_none());
        assert!(new_state.continuation.xsd_retry_session_reuse_pending);

        let effect = determine_next_effect(&new_state);
        assert!(
            matches!(effect, Effect::PrepareFixPrompt { .. }),
            "explicit fix drain should own XSD retry routing, got {effect:?}"
        );
    });
}

/// Test that same-agent timeout retry clears fix cleanup state from the explicit fix drain.
#[test]
fn test_fix_timeout_retry_recleans_fix_xml_from_fix_drain_without_review_issue_flag() {
    with_default_timeout(|| {
        let pass = 0;
        let mut agent_chain = AgentChainState::initial()
            .with_agents(vec!["codex".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(ralph_workflow::agents::AgentDrain::Fix);
        agent_chain.last_session_id = Some("session-123".to_string());

        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: pass,
            total_reviewer_passes: 1,
            review_issues_found: false,
            review_required_files_cleaned_pass: Some(pass),
            fix_prompt_prepared_pass: Some(pass),
            fix_required_files_cleaned_pass: Some(pass),
            agent_chain,
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::agent_timed_out(
                AgentRole::Reviewer,
                "codex".into(),
                ralph_workflow::reducer::event::TimeoutOutputKind::PartialOutput,
                Some(".agent/logs/reviewer_0.log".to_string()),
                None,
            ),
        );

        assert!(new_state.fix_required_files_cleaned_pass.is_none());
        assert_eq!(new_state.review_required_files_cleaned_pass, Some(pass));
        assert!(matches!(
            determine_next_effect(&new_state),
            Effect::PrepareFixPrompt { .. }
        ));
    });
}

/// Test that fix XSD exhaustion triggers agent fallback.
#[test]
fn test_fix_xsd_exhausted_triggers_fallback() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            review_issues_found: true,
            fix_prompt_prepared_pass: Some(0),
            fix_agent_invoked_pass: Some(0),
            continuation: ContinuationState {
                xsd_retry_count: 9,
                max_xsd_retry_count: 10,
                ..ContinuationState::new()
            },
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["fixer1".to_string(), "fixer2".to_string()],
                    vec![vec![], vec![]],
                    AgentRole::Reviewer,
                )
                .with_session_id(Some("session".to_string())),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::fix_output_validation_failed(0, 0, None),
        );

        assert_eq!(new_state.agent_chain.current_agent_index, 1);
        assert!(new_state.agent_chain.last_session_id.is_none());
        assert!(new_state.fix_prompt_prepared_pass.is_none());
        assert!(new_state.fix_agent_invoked_pass.is_none());
    });
}

// ============================================================================
// DEVELOPMENT PHASE XSD RETRY TESTS
// ============================================================================

/// Test that development XSD validation failure triggers analysis agent re-invocation.
///
/// Note: Development XSD retry is for the ANALYSIS agent output, not the developer.
/// Developer progress should be preserved; only analysis is retried.
#[test]
fn test_development_xsd_retry_preserves_developer_progress() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 1,
            total_iterations: 5,
            development_context_prepared_iteration: Some(1),
            development_prompt_prepared_iteration: Some(1),
            development_required_files_cleaned_iteration: Some(1),
            development_agent_invoked_iteration: Some(1), // Developer done
            analysis_agent_invoked_iteration: Some(1),    // Analysis done but failed
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["analyzer".to_string()],
                    vec![vec![]],
                    AgentRole::Analysis,
                )
                .with_session_id(Some("analysis-session".to_string())),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::development_output_validation_failed(1, 0),
        );

        // Verify: Developer progress PRESERVED
        assert_eq!(
            new_state.development_agent_invoked_iteration,
            Some(1),
            "Developer progress should be preserved"
        );

        // Verify: Analysis reset for retry
        assert!(
            new_state.analysis_agent_invoked_iteration.is_none(),
            "Analysis agent should be reset for retry"
        );

        // Verify: Session reuse enabled for analysis retry
        assert!(new_state.continuation.xsd_retry_session_reuse_pending);
    });
}

/// Test that development XSD exhaustion switches analysis agent.
#[test]
fn test_development_xsd_exhausted_switches_analysis_agent() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Development,
            iteration: 1,
            total_iterations: 5,
            development_agent_invoked_iteration: Some(1),
            analysis_agent_invoked_iteration: Some(1),
            continuation: ContinuationState {
                xsd_retry_count: 9,
                max_xsd_retry_count: 10,
                ..ContinuationState::new()
            },
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["analyzer1".to_string(), "analyzer2".to_string()],
                    vec![vec![], vec![]],
                    AgentRole::Analysis,
                )
                .with_session_id(Some("session".to_string())),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::development_output_validation_failed(1, 0),
        );

        // Verify: Agent switched
        assert_eq!(new_state.agent_chain.current_agent_index, 1);

        // Verify: Session cleared
        assert!(new_state.agent_chain.last_session_id.is_none());

        // Verify: Developer progress still preserved
        assert_eq!(new_state.development_agent_invoked_iteration, Some(1));

        // Verify: Analysis reset
        assert!(new_state.analysis_agent_invoked_iteration.is_none());
    });
}

// ============================================================================
// COMMIT PHASE XSD RETRY TESTS
// ============================================================================

/// Test that commit XSD validation failure triggers agent re-invocation.
#[test]
fn test_commit_xsd_retry_triggers_reinvocation() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::CommitMessage,
            commit: CommitState::Generating {
                attempt: 1,
                max_attempts: 3,
            },
            commit_prompt_prepared: true,
            commit_required_files_cleaned: true,
            commit_agent_invoked: true,
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["committer".to_string()],
                    vec![vec![]],
                    AgentRole::Commit,
                )
                .with_session_id(Some("commit-session".to_string())),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::commit_message_validation_failed("Invalid XML".to_string(), 1),
        );

        // Verify: Orchestration flags reset
        assert!(!new_state.commit_prompt_prepared);
        assert!(!new_state.commit_agent_invoked);

        // Verify: Session reuse enabled
        assert!(new_state.continuation.xsd_retry_session_reuse_pending);
        assert_eq!(
            new_state.agent_chain.last_session_id,
            Some("commit-session".to_string())
        );

        // Verify: Next effect prepares commit retry prompt
        let effect = determine_next_effect(&new_state);
        assert!(
            matches!(effect, Effect::PrepareCommitPrompt { .. }),
            "Should prepare commit XSD retry prompt, got {effect:?}"
        );
    });
}

/// Test that commit XSD exhaustion triggers agent fallback.
#[test]
fn test_commit_xsd_exhausted_triggers_fallback() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::CommitMessage,
            commit: CommitState::Generating {
                attempt: 1,
                max_attempts: 3,
            },
            commit_prompt_prepared: true,
            commit_agent_invoked: true,
            continuation: ContinuationState {
                xsd_retry_count: 9,
                max_xsd_retry_count: 10,
                ..ContinuationState::new()
            },
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["committer1".to_string(), "committer2".to_string()],
                    vec![vec![], vec![]],
                    AgentRole::Commit,
                )
                .with_session_id(Some("session".to_string())),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::commit_message_validation_failed("Invalid XML".to_string(), 1),
        );

        assert_eq!(new_state.agent_chain.current_agent_index, 1);
        assert!(new_state.agent_chain.last_session_id.is_none());
        assert!(!new_state.commit_prompt_prepared);
        assert!(!new_state.commit_agent_invoked);
    });
}

// ============================================================================
// FULL WORKFLOW TESTS
// ============================================================================

/// Test complete XSD retry workflow: failure -> retry -> success.
///
/// This test simulates the full flow:
/// 1. Agent is invoked and produces invalid XML
/// 2. XSD retry triggers with same session
/// 3. Agent is re-invoked
/// 4. Valid XML is produced
/// 5. Pipeline continues normally
#[test]
fn test_full_xsd_retry_workflow_planning() {
    with_default_timeout(|| {
        // Step 1: Setup - agent has been invoked
        let mut state = PipelineState {
            phase: PipelinePhase::Planning,
            iteration: 0,
            planning_prompt_prepared_iteration: Some(0),
            planning_required_files_cleaned_iteration: Some(0),
            planning_agent_invoked_iteration: Some(0),
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["claude".to_string()],
                    vec![vec![]],
                    AgentRole::Developer,
                )
                .with_session_id(Some("session-abc".to_string())),
            context_cleaned: true,
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        // Step 2: XSD validation fails
        state = reduce(
            state,
            PipelineEvent::planning_output_validation_failed(0, 0),
        );

        // Verify: XSD retry is pending with session reuse
        assert!(state.continuation.xsd_retry_pending);
        assert!(state.continuation.xsd_retry_session_reuse_pending);
        assert_eq!(state.continuation.xsd_retry_count, 1);

        // Step 3: Prompt is prepared (clears xsd_retry_pending but preserves session reuse)
        state = reduce(state, PipelineEvent::planning_prompt_prepared(0));

        assert!(!state.continuation.xsd_retry_pending);
        assert!(
            state.continuation.xsd_retry_session_reuse_pending,
            "Session reuse should persist until agent invocation"
        );

        // Step 4: Agent is re-invoked (clears session reuse pending)
        state = reduce(state, PipelineEvent::planning_agent_invoked(0));

        assert!(!state.continuation.xsd_retry_session_reuse_pending);
        assert_eq!(state.planning_agent_invoked_iteration, Some(0));

        // Step 5: Valid XML extracted
        state = reduce(state, PipelineEvent::planning_xml_extracted(0));

        // Step 6: Validation succeeds
        state = reduce(
            state,
            PipelineEvent::planning_xml_validated(0, true, Some("plan.md".to_string())),
        );

        // Step 7: Verify pipeline continues normally
        assert_eq!(state.continuation.xsd_retry_count, 1); // Count preserved for metrics
        assert!(!state.continuation.xsd_retry_pending);
    });
}

/// Test XSD retry exhaustion leads to agent fallback then success.
#[test]
fn test_full_xsd_exhaustion_to_fallback_success() {
    with_default_timeout(|| {
        // Setup: Near exhaustion
        let mut state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            review_prompt_prepared_pass: Some(0),
            review_required_files_cleaned_pass: Some(0),
            review_agent_invoked_pass: Some(0),
            continuation: ContinuationState {
                xsd_retry_count: 2,
                max_xsd_retry_count: 3,
                ..ContinuationState::new()
            },
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["agent1".to_string(), "agent2".to_string()],
                    vec![vec![], vec![]],
                    AgentRole::Reviewer,
                )
                .with_session_id(Some("session-1".to_string())),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        // XSD fails and exhausts retries
        state = reduce(
            state,
            PipelineEvent::review_output_validation_failed(0, 0, None),
        );

        // Verify: Switched to agent2
        assert_eq!(state.agent_chain.current_agent_index, 1);
        assert!(state.agent_chain.last_session_id.is_none());
        assert_eq!(state.continuation.xsd_retry_count, 0); // Reset for new agent

        // Agent2 prepares and invokes
        state = reduce(state, PipelineEvent::review_prompt_prepared(0));
        state = reduce(state, PipelineEvent::review_agent_invoked(0));

        // Simulate agent2 establishing a new session
        state = reduce(
            state,
            PipelineEvent::agent_session_established(
                AgentRole::Reviewer,
                "agent2".into(),
                "session-2".to_string(),
            ),
        );

        assert_eq!(
            state.agent_chain.last_session_id,
            Some("session-2".to_string())
        );

        // Agent2 succeeds
        state = reduce(state, PipelineEvent::review_issues_xml_extracted(0));
        state = reduce(
            state,
            PipelineEvent::review_issues_xml_validated(0, true, false, vec![], None),
        );

        // Pipeline continues
        assert_eq!(state.phase, PipelinePhase::Review);
    });
}

/// Test that missing XML files trigger XSD retry the same as validation failures.
#[test]
fn test_missing_xml_triggers_xsd_retry_same_as_validation_failure() {
    with_default_timeout(|| {
        // Test planning phase
        let planning_state = PipelineState {
            phase: PipelinePhase::Planning,
            iteration: 0,
            planning_agent_invoked_iteration: Some(0),
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["claude".to_string()],
                    vec![vec![]],
                    AgentRole::Developer,
                )
                .with_session_id(Some("session".to_string())),
            context_cleaned: true,
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(planning_state, PipelineEvent::planning_xml_missing(0, 0));

        // Same behavior as validation failure
        assert!(new_state.continuation.xsd_retry_pending);
        assert!(new_state.continuation.xsd_retry_session_reuse_pending);
        assert!(new_state.planning_agent_invoked_iteration.is_none());
    });
}

/// Test that same-agent retry state is reset when switching agents.
#[test]
fn test_xsd_exhaustion_resets_same_agent_retry_state() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Planning,
            iteration: 0,
            planning_agent_invoked_iteration: Some(0),
            continuation: ContinuationState {
                xsd_retry_count: 9,
                max_xsd_retry_count: 10,
                same_agent_retry_count: 2, // Has some same-agent retries
                same_agent_retry_pending: true,
                same_agent_retry_reason: Some(
                    ralph_workflow::reducer::state::SameAgentRetryReason::Timeout,
                ),
                ..ContinuationState::new()
            },
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["agent1".to_string(), "agent2".to_string()],
                    vec![vec![], vec![]],
                    AgentRole::Developer,
                )
                .with_session_id(Some("session".to_string())),
            context_cleaned: true,
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(5, 2),
            ))
        };

        let new_state = reduce(
            state,
            PipelineEvent::planning_output_validation_failed(0, 0),
        );

        // Verify: Same-agent retry state is reset
        assert_eq!(
            new_state.continuation.same_agent_retry_count, 0,
            "Same-agent retry count should reset on agent switch"
        );
        assert!(
            !new_state.continuation.same_agent_retry_pending,
            "Same-agent retry pending should be cleared"
        );
        assert!(
            new_state.continuation.same_agent_retry_reason.is_none(),
            "Same-agent retry reason should be cleared"
        );
    });
}
