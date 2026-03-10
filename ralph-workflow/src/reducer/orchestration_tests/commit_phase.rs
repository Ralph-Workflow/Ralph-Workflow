// Commit phase orchestration tests.
//
// Tests for commit phase: agent chain initialization, diff checking,
// prompt preparation, and commit creation.

use super::*;

#[test]
fn test_commit_empty_chain_initializes_agent_chain() {
    // When agent chain is empty, commit phase should request initialization
    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::NotStarted,
        agent_chain: crate::reducer::state::AgentChainState::initial(),
        ..create_test_state()
    };
    // Mark prompt permissions as already locked (prerequisite effects completed)
    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            role: AgentRole::Commit
        }
    ));
}

#[test]
fn test_commit_role_mismatch_initializes_commit_chain() {
    // Regression: Commit phase must not reuse developer/reviewer/analysis chains.
    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::NotStarted,
        agent_chain: crate::reducer::state::AgentChainState::initial().with_agents(
            vec!["dev-agent".to_string()],
            vec![vec![]],
            AgentRole::Developer,
        ),
        ..create_test_state()
    };
    // Mark prompt permissions as already locked (prerequisite effects completed)
    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            role: AgentRole::Commit
        }
    ));
}

#[test]
fn test_commit_not_started_prepares_prompt() {
    // With initialized agent chain, commit phase should prepare prompt
    use crate::reducer::state::AgentChainState;
    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::NotStarted,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };
    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::CheckCommitDiff));
}

#[test]
fn test_commit_checks_diff_before_prompt() {
    use crate::reducer::state::AgentChainState;
    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::NotStarted,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };
    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::CheckCommitDiff));
}

#[test]
fn test_commit_skips_when_diff_empty() {
    use crate::reducer::state::AgentChainState;
    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::NotStarted,
        commit_diff_prepared: true,
        commit_diff_empty: true,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };
    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::SkipCommit { .. }));
}

#[test]
fn test_commit_does_not_apply_outcome_without_xml_extracted() {
    use crate::reducer::state::{AgentChainState, CommitValidatedOutcome};
    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::Generating {
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
        commit_validated_outcome: Some(CommitValidatedOutcome {
            attempt: 1,
            message: Some("msg".to_string()),
            reason: None,
        }),
        prompt_inputs: crate::reducer::state::PromptInputsState {
            commit: Some(crate::reducer::state::MaterializedCommitInputs {
                attempt: 1,
                diff: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Diff,
                    content_id_sha256: "id".to_string(),
                    consumer_signature_sha256: String::new(),
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
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };

    let sig = state.agent_chain.consumer_signature_sha256();
    state
        .prompt_inputs
        .commit
        .as_mut()
        .unwrap()
        .diff
        .consumer_signature_sha256 = sig;

    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ExtractCommitXml));
}

#[test]
fn test_commit_generated_creates_commit() {
    use crate::reducer::state::AgentChainState;
    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::Generated {
            message: "test commit message".to_string(),
        },
        commit_xml_archived: true,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };
    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    let effect = determine_next_effect(&state);
    match effect {
        Effect::CreateCommit { message, files: _ } => {
            assert_eq!(message, "test commit message");
        }
        _ => panic!("Expected CreateCommit effect, got {effect:?}"),
    }
}

#[test]
fn test_commit_created_transitions_to_final_validation() {
    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::Generated {
            message: "test".to_string(),
        },
        ..create_test_state()
    };

    // Create commit
    state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "test".to_string()),
    );

    assert_eq!(state.phase, PipelinePhase::FinalValidation);
    assert!(matches!(
        state.commit,
        crate::reducer::state::CommitState::Committed { .. }
    ));
}

#[test]
fn test_commit_diff_prepared_invalidates_materialized_commit_inputs() {
    // Regression test: if a new commit diff is prepared (diff file rewritten),
    // previously materialized commit inputs must be invalidated so we don't reuse
    // stale materialization for the same attempt.
    use crate::reducer::state::AgentChainState;

    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::NotStarted,
        commit_diff_prepared: true,
        commit_diff_empty: false,
        prompt_inputs: crate::reducer::state::PromptInputsState {
            commit: Some(crate::reducer::state::MaterializedCommitInputs {
                attempt: 1,
                diff: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Diff,
                    content_id_sha256: "old".to_string(),
                    consumer_signature_sha256: String::new(),
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
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };

    // Make the consumer signature match so commit inputs would otherwise be considered valid.
    let sig = state.agent_chain.consumer_signature_sha256();
    state
        .prompt_inputs
        .commit
        .as_mut()
        .unwrap()
        .diff
        .consumer_signature_sha256 = sig;

    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    // Simulate diff being re-prepared (e.g. working tree changed) for the same attempt.
    state = reduce(
        state,
        PipelineEvent::commit_diff_prepared(false, "new".to_string()),
    );

    // Next effect should rematerialize inputs (not reuse stale materialization).
    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::MaterializeCommitInputs { attempt: 1 }),
        "Expected MaterializeCommitInputs after diff prepared, got {effect:?}"
    );
}

#[test]
fn test_create_commit_uses_selected_files_from_state() {
    // When commit_selected_files is populated in state, Effect::CreateCommit.files must
    // reflect those files exactly so the handler stages only the listed files.
    use crate::reducer::state::AgentChainState;

    let selected_files = vec!["src/foo.rs".to_string(), "tests/bar.rs".to_string()];

    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::Generated {
            message: "feat: add foo".to_string(),
        },
        commit_xml_archived: true,
        commit_selected_files: selected_files.clone(),
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };
    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    let effect = determine_next_effect(&state);
    match effect {
        Effect::CreateCommit { message, files } => {
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
fn test_commit_inputs_materialization_invalidated_when_diff_content_id_changes() {
    // Regression test: if the prepared diff content id changes (e.g. diff rewritten/updated),
    // we must not reuse previously materialized commit inputs for the same attempt even when
    // the consumer signature is unchanged.
    use crate::reducer::state::AgentChainState;

    let mut state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: crate::reducer::state::CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_diff_prepared: true,
        commit_diff_empty: false,
        commit_diff_content_id_sha256: Some("new".to_string()),
        prompt_inputs: crate::reducer::state::PromptInputsState {
            commit: Some(crate::reducer::state::MaterializedCommitInputs {
                attempt: 1,
                diff: crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Diff,
                    content_id_sha256: "old".to_string(),
                    consumer_signature_sha256: String::new(),
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
        agent_chain: AgentChainState::initial().with_agents(
            vec!["commit-agent".to_string()],
            vec![vec![]],
            AgentRole::Commit,
        ),
        ..create_test_state()
    };

    // Make the consumer signature match so commit inputs would otherwise be considered valid.
    let sig = state.agent_chain.consumer_signature_sha256();
    state
        .prompt_inputs
        .commit
        .as_mut()
        .unwrap()
        .diff
        .consumer_signature_sha256 = sig;

    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::MaterializeCommitInputs { attempt: 1 }),
        "Expected MaterializeCommitInputs when diff content id changes, got {effect:?}"
    );
}
