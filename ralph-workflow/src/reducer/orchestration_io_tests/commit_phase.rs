// Commit phase orchestration tests.
//
// Tests for commit phase: agent chain initialization, diff checking,
// prompt preparation, and commit creation.

use crate::agents::AgentRole;
use crate::reducer::effect::Effect;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::PipelinePhase;
use crate::reducer::io_tests::create_test_state;
use crate::reducer::orchestration::determine_next_effect;
use crate::reducer::state::AgentChainState;
use crate::reducer::state::CommitState;
use crate::reducer::state::PipelineState;
use crate::reducer::state::PromptInputsState;
use crate::reducer::state_reduction::reduce;

#[test]
fn test_commit_empty_chain_initializes_agent_chain() {
    // When agent chain is empty, commit phase should request initialization
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::NotStarted,
        agent_chain: AgentChainState::initial(),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Commit,
            ..
        }
    ));
}

#[test]
fn test_commit_role_mismatch_initializes_commit_chain() {
    // Regression: Commit phase must not reuse developer/reviewer/analysis chains.
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::NotStarted,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["dev-agent".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Commit,
            ..
        }
    ));
}

#[test]
fn test_commit_not_started_prepares_prompt() {
    // With initialized agent chain, commit phase should prepare prompt
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::NotStarted,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::CheckCommitDiff));
}

#[test]
fn test_commit_checks_diff_before_prompt() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::NotStarted,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::CheckCommitDiff));
}

#[test]
fn test_commit_skips_when_diff_empty() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::NotStarted,
        commit_diff_prepared: true,
        commit_diff_empty: true,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::SkipCommit { .. }));
}

#[test]
fn test_commit_does_not_apply_outcome_without_xml_extracted() {
    let agent_chain = AgentChainState::initial().with_agents(
        vec!["commit-agent".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );
    let sig = agent_chain.consumer_signature_sha256();
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_diff_prepared: true,
        commit_diff_empty: false,
        commit_diff_content_id_sha256: Some("id".to_string()),
        commit_prompt_prepared: true,
        commit_required_files_cleaned: true,
        commit_agent_invoked: true,
        commit_xml_extracted: false,
        commit_validated_outcome: Some(crate::reducer::state::CommitValidatedOutcome {
            attempt: 1,
            message: Some("msg".to_string()),
            reason: None,
        }),
        prompt_inputs: PromptInputsState {
            commit: Some(crate::reducer::state::MaterializedCommitInputs {
                attempt: 1,
                diff: crate::reducer::state::MaterializedPromptInput {
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
            }),
            ..Default::default()
        },
        agent_chain,
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ExtractCommitXml));
}

#[test]
fn test_commit_generated_creates_commit() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generated {
            message: "test commit message".to_string(),
        },
        commit_xml_archived: true,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    match effect {
        Effect::CreateCommit { message, .. } => {
            assert_eq!(message, "test commit message");
        }
        _ => panic!("Expected CreateCommit effect, got {effect:?}"),
    }
}

#[test]
fn test_commit_created_transitions_to_final_validation() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generated {
            message: "test".to_string(),
        },
        ..create_test_state()
    };

    let state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "test".to_string()),
    );

    assert_eq!(state.phase, PipelinePhase::FinalValidation);
    assert!(matches!(state.commit, CommitState::Committed { .. }));
}

#[test]
fn test_commit_diff_prepared_invalidates_materialized_commit_inputs() {
    let agent_chain = AgentChainState::initial().with_agents(
        vec!["commit-agent".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );
    let sig = agent_chain.consumer_signature_sha256();
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::NotStarted,
        commit_diff_prepared: true,
        commit_diff_empty: false,
        prompt_inputs: PromptInputsState {
            commit: Some(crate::reducer::state::MaterializedCommitInputs {
                attempt: 1,
                diff: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Diff,
                    content_id_sha256: "old".to_string(),
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
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let state = reduce(
        state,
        PipelineEvent::commit_diff_prepared(false, "new".to_string()),
    );

    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::MaterializeCommitInputs { attempt: 1 }),
        "Expected MaterializeCommitInputs after diff prepared, got {effect:?}"
    );
}

#[test]
fn test_create_commit_uses_selected_files_from_state() {
    let selected_files = vec!["src/foo.rs".to_string(), "tests/bar.rs".to_string()];

    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generated {
            message: "feat: add foo".to_string(),
        },
        commit_xml_archived: true,
        commit_selected_files: selected_files.clone(),
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    match effect {
        Effect::CreateCommit { message, files, .. } => {
            assert_eq!(message, "feat: add foo");
            assert_eq!(
                files, selected_files,
                "Effect::CreateCommit.files must equal state.commit_selected_files"
            );
        }
        _ => panic!("Expected CreateCommit effect, got {effect:?}"),
    }
}

#[test]
fn test_commit_xml_validated_with_files_propagates_to_create_commit() {
    let selected_files = vec![
        "src/auth/token.rs".to_string(),
        "tests/auth/token_test.rs".to_string(),
    ];

    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_xml_extracted: true,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };

    let state = reduce(
        state,
        PipelineEvent::commit_xml_validated(
            "fix(auth): prevent token expiry race".to_string(),
            selected_files.clone(),
            vec![],
            1,
        ),
    );
    assert_eq!(
        state.commit_selected_files, selected_files,
        "CommitXmlValidated must populate commit_selected_files"
    );

    let state = reduce(
        state,
        PipelineEvent::commit_message_generated(
            "fix(auth): prevent token expiry race".to_string(),
            1,
        ),
    );

    let state = reduce(state, PipelineEvent::commit_xml_archived(1));

    let effect = determine_next_effect(&state);
    match effect {
        Effect::CreateCommit { message, files, .. } => {
            assert_eq!(message, "fix(auth): prevent token expiry race");
            assert_eq!(
                files, selected_files,
                "Effect::CreateCommit.files must equal the files from CommitXmlValidated"
            );
        }
        _ => panic!("expected CreateCommit effect, got {effect:?}"),
    }
}

#[test]
fn test_commit_xml_validated_excluded_files_propagate_to_create_commit_effect() {
    let excluded_files = vec![crate::reducer::state::pipeline::ExcludedFile {
        path: ".agent/tmp/trace.log".to_string(),
        reason: crate::reducer::state::pipeline::ExcludedFileReason::InternalIgnore,
    }];

    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_xml_extracted: true,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };

    let state = reduce(
        state,
        PipelineEvent::commit_xml_validated(
            "feat: x".to_string(),
            vec![],
            excluded_files.clone(),
            1,
        ),
    );
    assert_eq!(state.commit_excluded_files, excluded_files);

    let state = reduce(
        state,
        PipelineEvent::commit_message_generated("feat: x".to_string(), 1),
    );
    let state = reduce(state, PipelineEvent::commit_xml_archived(1));

    let effect = determine_next_effect(&state);
    match effect {
        Effect::CreateCommit {
            excluded_files: got,
            ..
        } => {
            assert_eq!(got, excluded_files);
        }
        _ => panic!("expected CreateCommit effect, got {effect:?}"),
    }
}

#[test]
fn test_commit_generation_started_clears_selected_files() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit_selected_files: vec!["src/foo.rs".to_string()],
        commit_validated_outcome: Some(crate::reducer::state::CommitValidatedOutcome {
            attempt: 1,
            message: Some("feat: add foo".to_string()),
            reason: None,
        }),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let state = reduce(state, PipelineEvent::commit_generation_started());

    assert!(
        state.commit_selected_files.is_empty(),
        "commit_selected_files must be cleared when commit generation restarts"
    );
}

#[test]
fn test_commit_diff_invalidated_clears_selected_files() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit_selected_files: vec!["src/foo.rs".to_string()],
        commit_validated_outcome: Some(crate::reducer::state::CommitValidatedOutcome {
            attempt: 1,
            message: Some("feat: add foo".to_string()),
            reason: None,
        }),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let state = reduce(
        state,
        PipelineEvent::commit_diff_invalidated("changed".to_string()),
    );

    assert!(
        state.commit_selected_files.is_empty(),
        "commit_selected_files must be cleared when commit diff is invalidated"
    );
}

#[test]
fn test_commit_generation_failed_clears_selected_files() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit_selected_files: vec!["src/foo.rs".to_string()],
        commit_validated_outcome: Some(crate::reducer::state::CommitValidatedOutcome {
            attempt: 1,
            message: Some("feat: add foo".to_string()),
            reason: None,
        }),
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let state = reduce(
        state,
        PipelineEvent::commit_generation_failed("boom".to_string()),
    );

    assert!(
        state.commit_selected_files.is_empty(),
        "commit_selected_files must be cleared when commit generation fails"
    );
}

#[test]
fn test_commit_created_forced_resume_preserves_selected_files_until_residuals_complete() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        termination_resume_phase: Some(PipelinePhase::Finalizing),
        commit_selected_files: vec!["src/foo.rs".to_string()],
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let state = reduce(
        state,
        PipelineEvent::commit_created("deadbeef".to_string(), "feat: add foo".to_string()),
    );

    assert_eq!(
        state.phase,
        PipelinePhase::CommitMessage,
        "Selective forced commit must stay in CommitMessage so residual checking can run"
    );
    assert_eq!(
        state.termination_resume_phase,
        Some(PipelinePhase::Finalizing),
        "Resume phase must be preserved until residual handling completes"
    );
    assert!(
        !state.commit_selected_files.is_empty(),
        "commit_selected_files must be preserved until residual handling completes"
    );
}

#[test]
fn test_commit_skipped_forced_resume_clears_selected_files() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        termination_resume_phase: Some(PipelinePhase::Finalizing),
        commit_diff_empty: true,
        commit_selected_files: vec!["src/foo.rs".to_string()],
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let state = reduce(state, PipelineEvent::commit_skipped("nope".to_string()));

    assert!(
        state.commit_selected_files.is_empty(),
        "commit_selected_files must be cleared when leaving commit phase after skip"
    );
}

#[test]
fn test_commit_inputs_materialization_invalidated_when_diff_content_id_changes() {
    let agent_chain = AgentChainState::initial().with_agents(
        vec!["commit-agent".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );
    let sig = agent_chain.consumer_signature_sha256();
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_diff_prepared: true,
        commit_diff_empty: false,
        commit_diff_content_id_sha256: Some("new".to_string()),
        prompt_inputs: PromptInputsState {
            commit: Some(crate::reducer::state::MaterializedCommitInputs {
                attempt: 1,
                diff: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Diff,
                    content_id_sha256: "old".to_string(),
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
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::MaterializeCommitInputs { attempt: 1 }),
        "Expected MaterializeCommitInputs when diff content id changes, got {effect:?}"
    );
}
