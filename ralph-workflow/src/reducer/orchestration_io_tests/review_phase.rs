// Review phase orchestration tests.
//
// Tests for review phase: pass count, fix triggers, and skipping fix
// when no issues found.

use crate::agents::AgentRole;
use crate::common::domain_types::AgentName;
use crate::reducer::effect::Effect;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::PipelinePhase;
use crate::reducer::io_tests::create_test_state;
use crate::reducer::orchestration::determine_next_effect;
use crate::reducer::state::AgentChainState;
use crate::reducer::state::PipelineState;
use crate::reducer::state_reduction::reduce;

#[test]
fn test_review_runs_exactly_n_passes() {
    let agent_chain = PipelineState::initial(0, 3).agent_chain.with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Reviewer,
    );
    let state = PipelineState::initial(0, 3);

    let mut passes_run = Vec::new();
    let max_steps = 30;
    let mut current_state = PipelineState {
        agent_chain,
        ..state
    };

    let mut step = 0;
    while step < max_steps {
        let effect = determine_next_effect(&current_state);

        match effect {
            Effect::LockPromptPermissions => {
                current_state = reduce(
                    current_state,
                    PipelineEvent::prompt_permissions_locked(None),
                );
            }
            Effect::RestorePromptPermissions => {
                current_state = reduce(current_state, PipelineEvent::prompt_permissions_restored());
            }
            Effect::InitializeAgentChain { drain, .. } => {
                current_state = reduce(
                    current_state,
                    PipelineEvent::agent_chain_initialized(
                        drain,
                        vec![AgentName::from("claude")],
                        3,
                        1000,
                        2.0,
                        60000,
                    ),
                );
            }
            Effect::PrepareReviewContext { pass } => {
                passes_run.push(pass);
                current_state = reduce(current_state, PipelineEvent::review_context_prepared(pass));
                current_state = reduce(current_state, PipelineEvent::review_prompt_prepared(pass));
                current_state = reduce(
                    current_state,
                    PipelineEvent::review_issues_xml_cleaned(pass),
                );
                current_state = reduce(current_state, PipelineEvent::review_agent_invoked(pass));
                current_state = reduce(
                    current_state,
                    PipelineEvent::review_issues_xml_extracted(pass),
                );
                current_state = reduce(
                    current_state,
                    PipelineEvent::review_issues_xml_validated(
                        pass,
                        false,
                        true,
                        Vec::new(),
                        Some("ok".to_string()),
                    ),
                );
                current_state = reduce(
                    current_state,
                    PipelineEvent::review_issues_markdown_written(pass),
                );
                current_state = reduce(
                    current_state,
                    PipelineEvent::review_issue_snippets_extracted(pass),
                );
                current_state = reduce(
                    current_state,
                    PipelineEvent::review_issues_xml_archived(pass),
                );
                current_state = reduce(
                    current_state,
                    PipelineEvent::review_pass_completed_clean(pass),
                );
            }
            _ => break,
        }
        step += 1;
    }

    assert_eq!(
        passes_run.len(),
        3,
        "Should run exactly 3 review passes, ran: {passes_run:?}"
    );
    assert_eq!(passes_run, vec![0, 1, 2], "Should run passes 0-2");
    assert_eq!(
        current_state.phase,
        PipelinePhase::CommitMessage,
        "Should transition to CommitMessage after reviews"
    );
}

#[test]
fn test_review_triggers_fix_when_issues_found() {
    let mut state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        review_issues_found: false,
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        ),
        ..create_test_state()
    };

    // Initially should begin review chain
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareReviewContext { pass: 0 }));

    // Review completes with issues found
    state = reduce(state, PipelineEvent::review_completed(0, true));

    assert!(state.review_issues_found);

    // Review and fix are distinct drains, so a completed review with issues must
    // reinitialize into the fix drain before prompting.
    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Fix,
            ..
        }
    ));
    state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Fix,
            vec![AgentName::from("claude")],
            3,
            1000,
            2.0,
            60000,
        ),
    );
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareFixPrompt { pass: 0, .. }));

    // Fix completes - now transitions to CommitMessage phase
    state = reduce(state, PipelineEvent::fix_attempt_completed(0, true));

    assert!(!state.review_issues_found);
    assert_eq!(state.phase, PipelinePhase::CommitMessage);
    assert_eq!(
        state.previous_phase,
        Some(PipelinePhase::Review),
        "Should remember we came from Review"
    );
    // reviewer_pass stays at 0 until CommitCreated
    assert_eq!(state.reviewer_pass, 0);

    // Commit message chain: initialize commit agent chain first (role-specific).
    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Commit,
            ..
        }
    ));
    state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Commit,
            vec![AgentName::from("claude")],
            3,
            1000,
            2.0,
            60000,
        ),
    );

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::CheckCommitDiff));
    state = reduce(
        state,
        PipelineEvent::commit_diff_prepared(false, "id".to_string()),
    );
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::MaterializeCommitInputs { .. }));
    let sig = state.agent_chain.consumer_signature_sha256();
    state = reduce(
        state,
        PipelineEvent::commit_inputs_materialized(
            1,
            crate::reducer::state::MaterializedPromptInput {
                kind: crate::reducer::state::PromptInputKind::Diff,
                content_id_sha256: "id".to_string(),
                consumer_signature_sha256: sig,
                original_bytes: 1,
                final_bytes: 1,
                model_budget_bytes: None,
                inline_budget_bytes: None,
                representation: crate::reducer::state::PromptInputRepresentation::Inline,
                reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
            },
        ),
    );
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareCommitPrompt { .. }));
    state = reduce(state, PipelineEvent::commit_generation_started());
    state = reduce(state, PipelineEvent::commit_prompt_prepared(1));

    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::CleanupRequiredFiles { ref files } if files.iter().any(|f| f.contains("commit_message.xml")))
    );
    state = reduce(state, PipelineEvent::commit_required_files_cleaned(1));

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::InvokeCommitAgent));
    state = reduce(state, PipelineEvent::commit_agent_invoked(1));

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ExtractCommitXml));
    state = reduce(state, PipelineEvent::commit_xml_extracted(1));

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ValidateCommitXml));
    state = reduce(
        state,
        PipelineEvent::commit_xml_validated(
            "fix: address review issues".to_string(),
            vec![],
            vec![],
            1,
        ),
    );

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ApplyCommitMessageOutcome));
    state = reduce(
        state,
        PipelineEvent::commit_message_generated("fix: address review issues".to_string(), 1),
    );

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ArchiveCommitXml));
    state = reduce(state, PipelineEvent::commit_xml_archived(1));

    // Create commit
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::CreateCommit { .. }));
    state = reduce(
        state,
        PipelineEvent::commit_created(
            "abc123".to_string(),
            "fix: address review issues".to_string(),
        ),
    );

    // Now we're back in Review with incremented pass
    assert_eq!(state.reviewer_pass, 1);
    assert_eq!(state.phase, PipelinePhase::Review);
}

#[test]
fn test_review_skips_fix_when_no_issues() {
    let mut state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        review_issues_found: false,
        agent_chain: PipelineState::initial(5, 2).agent_chain.with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        ),
        ..create_test_state()
    };

    // Begin review chain
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareReviewContext { pass: 0 }));

    // Review completes with NO issues
    state = reduce(state, PipelineEvent::review_completed(0, false));

    assert!(!state.review_issues_found);
    assert_eq!(
        state.reviewer_pass, 1,
        "Should increment to next pass when no issues"
    );

    // Should begin next review chain (pass 1), NOT fix chain
    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::PrepareReviewContext { pass: 1 }),
        "Expected PrepareReviewContext pass 1, got {effect:?}"
    );
}

#[test]
fn test_review_with_issues_initializes_fix_drain_when_chain_targets_review() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: true,
        agent_chain: PipelineState::initial(1, 1)
            .agent_chain
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Fix),
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareFixPrompt { .. }));
}

#[test]
fn test_review_context_prepared_invalidates_materialized_review_inputs() {
    let agent_chain = AgentChainState::initial().with_agents(
        vec!["reviewer".to_string()],
        vec![vec![]],
        AgentRole::Reviewer,
    );
    let sig = agent_chain.consumer_signature_sha256();
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_context_prepared_pass: Some(0),
        prompt_inputs: crate::reducer::state::PromptInputsState {
            review: Some(crate::reducer::state::MaterializedReviewInputs {
                pass: 0,
                plan: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Plan,
                    content_id_sha256: "plan".to_string(),
                    consumer_signature_sha256: sig.clone(),
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                },
                diff: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Diff,
                    content_id_sha256: "old-diff".to_string(),
                    consumer_signature_sha256: sig,
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                },
            }),
            ..Default::default()
        },
        agent_chain,
        ..create_test_state()
    };

    let state = reduce(state, PipelineEvent::review_context_prepared(0));

    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::MaterializeReviewInputs { pass: 0 }),
        "Expected MaterializeReviewInputs after context prepared, got {effect:?}"
    );
}
