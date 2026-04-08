// Review phase tests.
//
// Tests for review phase effect determination, agent chain states,
// fix triggering, and pass counting.

use super::*;
use crate::reducer::state::ReviewValidatedOutcome;

#[test]
fn test_determine_effect_review_phase_empty_chain() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 1,
        total_reviewer_passes: 2,
        agent_chain: AgentChainState::initial(),
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Review,
            ..
        }
    ));
}

#[test]
fn test_determine_effect_review_phase_exhausted_chain() {
    let mut chain = AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        )
        .with_drain(crate::agents::AgentDrain::Review)
        .with_max_cycles(3);
    chain = chain.start_retry_cycle();
    chain = chain.start_retry_cycle();
    chain = chain.start_retry_cycle();

    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 1,
        total_reviewer_passes: 2,
        agent_chain: chain,
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::SaveCheckpoint { .. }));
}

#[test]
fn test_determine_effect_review_exhausted_chain_after_checkpoint_aborts() {
    let mut chain = AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        )
        .with_drain(crate::agents::AgentDrain::Review)
        .with_max_cycles(3);
    chain = chain.start_retry_cycle();
    chain = chain.start_retry_cycle();
    chain = chain.start_retry_cycle();

    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 1,
        total_reviewer_passes: 2,
        checkpoint_saved_count: 1,
        agent_chain: chain,
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ReportAgentChainExhausted { .. }));
}

#[test]
fn test_determine_effect_review_phase_with_chain() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 1,
        total_reviewer_passes: 2,
        agent_chain: PipelineState::initial(5, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Review),
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareReviewContext { pass: 1 }));
}

#[test]
fn test_resume_scenario_at_final_review_pass_runs_work() {
    // Test RESUME-ONLY scenario: reviewer_pass == total_reviewer_passes with no progress flags.
    // This simulates resuming from a checkpoint saved at the final review pass.
    // Orchestration should re-run the review work (resume behavior),
    // not skip to SaveCheckpoint.
    //
    // This is distinct from fresh-run behavior where if all progress flags indicate
    // completion (archived == Some(pass)), orchestration should ApplyReviewOutcome
    // instead of re-running the work.
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 2,
        total_reviewer_passes: 2,
        agent_chain: PipelineState::initial(5, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Review),
        // Explicitly set all progress flags to None to simulate resume state
        review_context_prepared_pass: None,
        review_prompt_prepared_pass: None,
        review_required_files_cleaned_pass: None,
        review_agent_invoked_pass: None,
        review_issues_xml_extracted_pass: None,
        review_validated_outcome: None,
        review_issues_markdown_written_pass: None,
        review_issue_snippets_extracted_pass: None,
        review_issues_xml_archived_pass: None,
        fix_prompt_prepared_pass: None,
        fix_required_files_cleaned_pass: None,
        fix_agent_invoked_pass: None,
        fix_result_xml_extracted_pass: None,
        fix_validated_outcome: None,
        fix_result_xml_archived_pass: None,
        ..create_test_state()
    };
    let effect = determine_next_effect(&state);
    // Should derive review work, not SaveCheckpoint
    assert!(matches!(effect, Effect::PrepareReviewContext { .. }));
}

#[test]
fn test_review_triggers_fix_when_issues_found() {
    // Create state in Review phase with issues found
    let mut state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        review_issues_found: false,
        agent_chain: PipelineState::initial(5, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Review),
        ..create_test_state()
    };

    // Initially should begin review chain
    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::PrepareReviewContext { pass: 0 }),
        "Expected PrepareReviewContext, got {effect:?}"
    );

    // Simulate review completing with issues found
    state = reduce(state, PipelineEvent::review_completed(0, true));

    // State should now have issues_found flag set
    assert!(
        state.review_issues_found,
        "review_issues_found should be true"
    );

    // With explicit drains, review completion with issues should initialize the fix drain first.
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            Effect::InitializeAgentChain {
                drain: crate::agents::AgentDrain::Fix,
                ..
            }
        ),
        "Expected fix drain initialization after issues found, got {effect:?}"
    );

    // After fix completes, goes to CommitMessage phase
    state = reduce(state, PipelineEvent::fix_attempt_completed(0, true));

    assert!(
        !state.review_issues_found,
        "review_issues_found should be reset after fix"
    );
    // After fix, goes to CommitMessage phase (pass increment happens after commit)
    assert_eq!(
        state.reviewer_pass, 0,
        "Pass stays at 0 until CommitCreated"
    );
    assert_eq!(
        state.phase,
        PipelinePhase::CommitMessage,
        "Should go to CommitMessage phase after fix"
    );

    // After commit is created, pass is incremented
    state = reduce(state, PipelineEvent::commit_generation_started());
    state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "fix commit".to_string()),
    );

    assert_eq!(
        state.reviewer_pass, 1,
        "Should increment to next pass after commit"
    );
    assert_eq!(
        state.phase,
        PipelinePhase::Review,
        "Should return to Review phase after commit"
    );
}

#[test]
fn test_review_runs_exactly_n_passes() {
    // Similar to development iteration test, verify review passes count
    let mut state = PipelineState::initial(0, 3); // 0 dev, 3 review passes
    state.agent_chain = state
        .agent_chain
        .with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        )
        .with_drain(crate::agents::AgentDrain::Review);

    let mut passes_run = Vec::new();
    let max_steps = 30;

    for _ in 0..max_steps {
        let effect = determine_next_effect(&state);

        match effect {
            Effect::LockPromptPermissions => {
                state = reduce(state, PipelineEvent::prompt_permissions_locked(None));
            }
            Effect::RestorePromptPermissions => {
                state = reduce(state, PipelineEvent::prompt_permissions_restored());
            }
            Effect::InitializeAgentChain { drain, .. } => {
                state = reduce(
                    state,
                    PipelineEvent::agent_chain_initialized(
                        drain,
                        vec![AgentName::from("claude")],
                        vec![],
                        3,
                        1000,
                        2.0,
                        60000,
                    ),
                );
            }
            Effect::PrepareReviewContext { pass } => {
                passes_run.push(pass);
                state = reduce(state, PipelineEvent::review_context_prepared(pass));
                state = reduce(state, PipelineEvent::review_prompt_prepared(pass));
                state = reduce(state, PipelineEvent::review_issues_xml_cleaned(pass));
                state = reduce(state, PipelineEvent::review_agent_invoked(pass));
                state = reduce(state, PipelineEvent::review_issues_xml_extracted(pass));
                state = reduce(
                    state,
                    PipelineEvent::review_issues_xml_validated(
                        pass,
                        false,
                        true,
                        Vec::new(),
                        Some("ok".to_string()),
                    ),
                );
                state = reduce(state, PipelineEvent::review_issues_markdown_written(pass));
                state = reduce(state, PipelineEvent::review_issue_snippets_extracted(pass));
                state = reduce(state, PipelineEvent::review_issues_xml_archived(pass));
                state = reduce(state, PipelineEvent::review_pass_completed_clean(pass));
            }
            Effect::SaveCheckpoint { .. } => {
                // Review complete
                break;
            }
            _ => break,
        }
    }

    assert_eq!(
        passes_run.len(),
        3,
        "Should run exactly 3 review passes, ran: {passes_run:?}"
    );
    assert_eq!(passes_run, vec![0, 1, 2], "Should run passes 0-2");
    assert_eq!(
        state.phase,
        PipelinePhase::CommitMessage,
        "Should transition to CommitMessage after reviews"
    );
}

#[test]
fn test_review_skips_fix_when_no_issues() {
    // Create state in Review phase
    let mut state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        review_issues_found: false,
        agent_chain: PipelineState::initial(5, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Review),
        ..create_test_state()
    };

    // Begin review chain
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareReviewContext { pass: 0 }));

    // Review completes with NO issues
    state = reduce(state, PipelineEvent::review_completed(0, false));

    assert!(
        !state.review_issues_found,
        "review_issues_found should be false"
    );

    assert_eq!(
        state.reviewer_pass, 1,
        "Should increment to next pass when no issues"
    );

    // Should begin next review chain (pass 1), NOT fix chain
    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::PrepareReviewContext { pass: 1 }),
        "Expected PrepareReviewContext pass 1 when no issues, got {effect:?}"
    );
}

#[test]
fn test_same_agent_retry_in_fix_drain_uses_fix_prompt_without_review_flags() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 1,
        total_reviewer_passes: 2,
        review_issues_found: false,
        agent_chain: PipelineState::initial(5, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Fix)
            .with_mode(crate::agents::DrainMode::SameAgentRetry),
        continuation: crate::reducer::state::ContinuationState {
            same_agent_retry_pending: true,
            ..crate::reducer::state::ContinuationState::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::PrepareFixPrompt { pass: 1, .. }),
        "expected fix retry prompt for fix drain, got {effect:?}"
    );

    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 1,
        total_reviewer_passes: 2,
        review_issues_found: false,
        agent_chain: PipelineState::initial(5, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Review),
        continuation: crate::reducer::state::ContinuationState {
            fix_continue_pending: true,
            fix_continuation_attempt: 1,
            ..crate::reducer::state::ContinuationState::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    assert!(
        matches!(
            effect,
            Effect::InitializeAgentChain {
                drain: crate::agents::AgentDrain::Fix,
            }
        ),
        "fix continuation markers should reinitialize the fix drain when review is still loaded, got {effect:?}"
    );
}

#[test]
fn test_fix_continuation_from_analysis_prepares_prompt_before_chain_reinit() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 1,
        total_reviewer_passes: 2,
        review_issues_found: true,
        agent_chain: AgentChainState::initial()
            .with_drain(crate::agents::AgentDrain::Review)
            .with_mode(crate::agents::DrainMode::Continuation),
        continuation: crate::reducer::state::ContinuationState {
            fix_continue_pending: true,
            fix_continuation_attempt: 1,
            ..crate::reducer::state::ContinuationState::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    assert!(
        matches!(
            effect,
            Effect::PrepareFixPrompt {
                pass: 1,
                prompt_mode: crate::reducer::state::PromptMode::Continuation,
            }
        ),
        "analysis->fix continuation should prepare continuation prompt before reinitializing chain, got {effect:?}"
    );
}

#[test]
fn test_determine_effect_review_phase_with_wrong_role_chain() {
    // Scenario: Review phase with a non-empty chain, but the role is Commit
    // This simulates the bug where we transition from CommitMessage back to Review
    // and the chain was left as AgentRole::Commit
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 1,
        total_reviewer_passes: 2,
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit, // Wrong role!
        ),
        ..create_test_state()
    };

    // Should initialize a new chain for Reviewer role, not use the Commit chain
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            Effect::InitializeAgentChain {
                drain: crate::agents::AgentDrain::Review,
                ..
            }
        ),
        "Expected InitializeAgentChain for the review drain, got {effect:?}"
    );
}

#[test]
fn test_resume_at_final_review_pass_should_run_review_not_skip() {
    // BUG REPRODUCTION: When checkpoint saved at reviewer_pass=2, total=2
    // and all progress flags are None (reset on resume),
    // orchestration should derive review work effects,
    // NOT SaveCheckpoint (which would skip to next phase).

    let state = PipelineState {
        phase: PipelinePhase::Review,
        iteration: 3,
        total_iterations: 3,
        reviewer_pass: 2,
        total_reviewer_passes: 2,
        review_issues_found: false,
        agent_chain: PipelineState::initial(3, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Review),
        // All progress flags None - simulating resume state
        review_context_prepared_pass: None,
        review_prompt_prepared_pass: None,
        review_required_files_cleaned_pass: None,
        review_agent_invoked_pass: None,
        review_issues_xml_extracted_pass: None,
        review_validated_outcome: None,
        review_issues_markdown_written_pass: None,
        review_issue_snippets_extracted_pass: None,
        review_issues_xml_archived_pass: None,
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    // CRITICAL: Should derive review work, NOT phase transition
    // This test verifies the fix: previously would fail, now passes
    assert!(
        matches!(effect, Effect::PrepareReviewContext { .. }),
        "Expected PrepareReviewContext, got {effect:?}"
    );
}

#[test]
fn test_resume_at_final_review_pass_with_no_progress_should_run_review() {
    // Bug scenario: checkpoint saved at reviewer_pass=2, total=2
    // On resume, all progress flags are None (reset)
    // Expected: Should re-run review pass
    // Actual (bug): Skips to SaveCheckpoint, then transitions to next phase

    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 2,
        total_reviewer_passes: 2,
        review_issues_found: false,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Review),
        // All progress flags are None (simulating resume state)
        review_context_prepared_pass: None,
        review_prompt_prepared_pass: None,
        review_agent_invoked_pass: None,
        review_issues_xml_archived_pass: None,
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    // Should prepare review context (start pass), NOT save checkpoint
    assert!(
        matches!(effect, Effect::PrepareReviewContext { pass: 2 }),
        "Expected PrepareReviewContext but got {effect:?}"
    );
}

#[test]
fn test_resume_at_review_pass_zero_with_total_one_runs_work() {
    // Boundary case: reviewer_pass=0, total_reviewer_passes=1

    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: false,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Review),
        review_agent_invoked_pass: None,
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    assert!(
        matches!(effect, Effect::PrepareReviewContext { pass: 0 }),
        "Expected PrepareReviewContext but got {effect:?}"
    );
}

#[test]
fn test_review_pass_completed_applies_outcome_not_reruns() {
    // Verify that when reviewer_pass == total_reviewer_passes AND the work is
    // actually complete (review_issues_xml_archived_pass is Some), orchestration
    // should apply the review outcome (to transition to the next phase), not re-run the work.
    //
    // This is the "truly complete" scenario, distinct from the resume scenario
    // where all progress flags are None.
    //
    // When the review pass is complete:
    // - Orchestration derives ApplyReviewOutcome (to process the outcome)
    // - The outcome handler may then trigger a phase transition

    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 2,
        total_reviewer_passes: 2,
        review_issues_found: false,
        agent_chain: PipelineState::initial(3, 2)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Review),
        // Work is complete - archived flag is set
        review_issues_xml_archived_pass: Some(2),
        // Other progress flags set to indicate completion
        review_context_prepared_pass: Some(2),
        review_prompt_prepared_pass: Some(2),
        review_required_files_cleaned_pass: Some(2),
        review_agent_invoked_pass: Some(2),
        review_issues_xml_extracted_pass: Some(2),
        review_validated_outcome: Some(ReviewValidatedOutcome {
            pass: 2,
            issues_found: false,
            clean_no_issues: true,
            issues: Vec::new().into_boxed_slice(),
            no_issues_found: Some("ok".to_string()),
        }),
        review_issues_markdown_written_pass: Some(2),
        review_issue_snippets_extracted_pass: Some(2),
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);

    // Should apply the outcome (which will trigger transition), NOT re-run the review work
    assert!(
        matches!(
            effect,
            Effect::ApplyReviewOutcome {
                pass: 2,
                issues_found: false,
                clean_no_issues: true
            }
        ),
        "Expected ApplyReviewOutcome for completed review, got {effect:?}"
    );
}
