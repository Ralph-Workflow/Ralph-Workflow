// Review phase single-task effect chain tests.
//
// Tests for review phase effect emission: initialize chain, prepare context,
// prepare prompt, invoke agent, extract/validate XML, write markdown, etc.

use crate::agents::AgentRole;
use crate::reducer::effect::Effect;
use crate::reducer::event::PipelineEvent;
use crate::reducer::orchestration::determine_next_effect;
use crate::reducer::state::PipelineState;
use crate::reducer::state_reduction::reduce;

fn initial_with_locked_permissions(dev_iters: u32, review_passes: u32) -> PipelineState {
    PipelineState {
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..PipelineState::initial(dev_iters, review_passes)
    }
}

fn dummy_input(
    kind: crate::reducer::state::PromptInputKind,
    consumer_signature_sha256: String,
) -> crate::reducer::state::MaterializedPromptInput {
    crate::reducer::state::MaterializedPromptInput {
        kind,
        content_id_sha256: "id".to_string(),
        consumer_signature_sha256,
        original_bytes: 1,
        final_bytes: 1,
        model_budget_bytes: None,
        inline_budget_bytes: None,
        representation: crate::reducer::state::PromptInputRepresentation::Inline,
        reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
    }
}

#[test]
fn test_review_phase_emits_initialize_chain_then_prepare_review_context() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        ..initial_with_locked_permissions(1, 1)
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::InitializeAgentChain {
            drain: crate::agents::AgentDrain::Review
        }
    ));

    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec!["mock".to_string()],
            1,
            0,
            1.0,
            0,
        ),
    );
    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareReviewContext { pass: 0 }));
}

#[test]
fn test_review_phase_emits_prepare_review_context_after_chain_initialized() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        ..initial_with_locked_permissions(1, 1)
    };

    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec!["mock".to_string()],
            1,
            0,
            1.0,
            0,
        ),
    );

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareReviewContext { pass: 0 }));
}

#[test]
fn test_review_phase_emits_cleanup_required_files_after_prompt_prepared() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        ..initial_with_locked_permissions(1, 1)
    };

    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec!["mock".to_string()],
            1,
            0,
            1.0,
            0,
        ),
    );
    let state = reduce(state, PipelineEvent::review_context_prepared(0));
    let sig = state.agent_chain.consumer_signature_sha256();
    let state = reduce(
        state,
        PipelineEvent::review_inputs_materialized(
            0,
            dummy_input(crate::reducer::state::PromptInputKind::Plan, sig.clone()),
            dummy_input(crate::reducer::state::PromptInputKind::Diff, sig),
        ),
    );
    let state = reduce(state, PipelineEvent::review_prompt_prepared(0));

    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::CleanupRequiredFiles { files } if files.iter().any(|f| f.contains("issues.xml")))
    );
}

#[test]
fn test_review_phase_emits_extract_review_issues_xml_after_agent_invoked() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        ..initial_with_locked_permissions(1, 1)
    };

    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec!["mock".to_string()],
            1,
            0,
            1.0,
            0,
        ),
    );
    let state = reduce(state, PipelineEvent::review_context_prepared(0));
    let sig = state.agent_chain.consumer_signature_sha256();
    let state = reduce(
        state,
        PipelineEvent::review_inputs_materialized(
            0,
            dummy_input(crate::reducer::state::PromptInputKind::Plan, sig.clone()),
            dummy_input(crate::reducer::state::PromptInputKind::Diff, sig),
        ),
    );
    let state = reduce(state, PipelineEvent::review_prompt_prepared(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_cleaned(0));
    let state = reduce(state, PipelineEvent::review_agent_invoked(0));

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ExtractReviewIssuesXml { pass: 0 }));
}

#[test]
fn test_review_phase_emits_validate_review_issues_xml_after_extracted() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        ..initial_with_locked_permissions(1, 1)
    };

    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec!["mock".to_string()],
            1,
            0,
            1.0,
            0,
        ),
    );
    let state = reduce(state, PipelineEvent::review_context_prepared(0));
    let sig = state.agent_chain.consumer_signature_sha256();
    let state = reduce(
        state,
        PipelineEvent::review_inputs_materialized(
            0,
            dummy_input(crate::reducer::state::PromptInputKind::Plan, sig.clone()),
            dummy_input(crate::reducer::state::PromptInputKind::Diff, sig),
        ),
    );
    let state = reduce(state, PipelineEvent::review_prompt_prepared(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_cleaned(0));
    let state = reduce(state, PipelineEvent::review_agent_invoked(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_extracted(0));

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::ValidateReviewIssuesXml { pass: 0 }
    ));
}

#[test]
fn test_review_phase_emits_write_issues_markdown_after_validated() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        ..initial_with_locked_permissions(1, 1)
    };

    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec!["mock".to_string()],
            1,
            0,
            1.0,
            0,
        ),
    );
    let state = reduce(state, PipelineEvent::review_context_prepared(0));
    let sig = state.agent_chain.consumer_signature_sha256();
    let state = reduce(
        state,
        PipelineEvent::review_inputs_materialized(
            0,
            dummy_input(crate::reducer::state::PromptInputKind::Plan, sig.clone()),
            dummy_input(crate::reducer::state::PromptInputKind::Diff, sig),
        ),
    );
    let state = reduce(state, PipelineEvent::review_prompt_prepared(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_cleaned(0));
    let state = reduce(state, PipelineEvent::review_agent_invoked(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_extracted(0));
    let state = reduce(
        state,
        PipelineEvent::review_issues_xml_validated(
            0,
            false,
            true,
            Vec::new(),
            Some("ok".to_string()),
        ),
    );

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::WriteIssuesMarkdown { pass: 0 }));
}

#[test]
fn test_review_phase_emits_extract_issue_snippets_after_markdown_written() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        ..initial_with_locked_permissions(1, 1)
    };

    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec!["mock".to_string()],
            1,
            0,
            1.0,
            0,
        ),
    );
    let state = reduce(state, PipelineEvent::review_context_prepared(0));
    let sig = state.agent_chain.consumer_signature_sha256();
    let state = reduce(
        state,
        PipelineEvent::review_inputs_materialized(
            0,
            dummy_input(crate::reducer::state::PromptInputKind::Plan, sig.clone()),
            dummy_input(crate::reducer::state::PromptInputKind::Diff, sig),
        ),
    );
    let state = reduce(state, PipelineEvent::review_prompt_prepared(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_cleaned(0));
    let state = reduce(state, PipelineEvent::review_agent_invoked(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_extracted(0));
    let state = reduce(
        state,
        PipelineEvent::review_issues_xml_validated(
            0,
            false,
            true,
            Vec::new(),
            Some("ok".to_string()),
        ),
    );
    let state = reduce(state, PipelineEvent::review_issues_markdown_written(0));

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::ExtractReviewIssueSnippets { pass: 0 }
    ));
}

#[test]
fn test_review_phase_emits_archive_issues_xml_after_snippets_extracted() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        ..initial_with_locked_permissions(1, 1)
    };

    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec!["mock".to_string()],
            1,
            0,
            1.0,
            0,
        ),
    );
    let state = reduce(state, PipelineEvent::review_context_prepared(0));
    let sig = state.agent_chain.consumer_signature_sha256();
    let state = reduce(
        state,
        PipelineEvent::review_inputs_materialized(
            0,
            dummy_input(crate::reducer::state::PromptInputKind::Plan, sig.clone()),
            dummy_input(crate::reducer::state::PromptInputKind::Diff, sig),
        ),
    );
    let state = reduce(state, PipelineEvent::review_prompt_prepared(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_cleaned(0));
    let state = reduce(state, PipelineEvent::review_agent_invoked(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_extracted(0));
    let state = reduce(
        state,
        PipelineEvent::review_issues_xml_validated(
            0,
            false,
            true,
            Vec::new(),
            Some("ok".to_string()),
        ),
    );
    let state = reduce(state, PipelineEvent::review_issues_markdown_written(0));
    let state = reduce(state, PipelineEvent::review_issue_snippets_extracted(0));

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ArchiveReviewIssuesXml { pass: 0 }));
}

#[test]
fn test_review_phase_emits_apply_review_outcome_after_issues_xml_archived() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        ..initial_with_locked_permissions(1, 1)
    };

    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec!["mock".to_string()],
            1,
            0,
            1.0,
            0,
        ),
    );
    let state = reduce(state, PipelineEvent::review_context_prepared(0));
    let sig = state.agent_chain.consumer_signature_sha256();
    let state = reduce(
        state,
        PipelineEvent::review_inputs_materialized(
            0,
            dummy_input(crate::reducer::state::PromptInputKind::Plan, sig.clone()),
            dummy_input(crate::reducer::state::PromptInputKind::Diff, sig),
        ),
    );
    let state = reduce(state, PipelineEvent::review_prompt_prepared(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_cleaned(0));
    let state = reduce(state, PipelineEvent::review_agent_invoked(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_extracted(0));
    let state = reduce(
        state,
        PipelineEvent::review_issues_xml_validated(
            0,
            false,
            true,
            Vec::new(),
            Some("ok".to_string()),
        ),
    );
    let state = reduce(state, PipelineEvent::review_issues_markdown_written(0));
    let state = reduce(state, PipelineEvent::review_issue_snippets_extracted(0));
    let state = reduce(state, PipelineEvent::review_issues_xml_archived(0));

    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            Effect::ApplyReviewOutcome {
                pass: 0,
                issues_found: false,
                clean_no_issues: true
            }
        ),
        "unexpected effect: {effect:?}"
    );
}

#[test]
fn test_review_phase_emits_prepare_review_prompt_after_context_prepared() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        ..initial_with_locked_permissions(1, 1)
    };

    let state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            vec!["mock".to_string()],
            1,
            0,
            1.0,
            0,
        ),
    );
    let state = reduce(state, PipelineEvent::review_context_prepared(0));

    let effect = determine_next_effect(&state);
    assert!(matches!(
        effect,
        Effect::MaterializeReviewInputs { pass: 0 }
    ));
}
