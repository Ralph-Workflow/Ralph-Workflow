// Fix chain single-task effect tests.
//
// Tests for fix chain effect emission: prepare fix prompt, cleanup XML,
// invoke agent, extract/validate XML, archive XML, apply outcome.

use crate::agents::AgentRole;
use crate::reducer::effect::Effect;
use crate::reducer::event::PipelineEvent;
use crate::reducer::event::PipelinePhase;
use crate::reducer::io_tests::create_test_state;
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

#[test]
fn test_review_with_issues_emits_prepare_fix_prompt() {
    // When review finds issues, the pipeline should enter the fix chain as single-task effects.
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: true,
        agent_chain: PipelineState::initial(1, 1)
            .agent_chain
            .with_agents(vec!["mock".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(crate::agents::AgentDrain::Fix),
        ..initial_with_locked_permissions(1, 1)
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::PrepareFixPrompt { pass: 0, .. }));
}

#[test]
fn test_fix_chain_emits_cleanup_fix_result_xml_after_fix_prompt_prepared() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: true,
        fix_prompt_prepared_pass: Some(0),
        agent_chain: PipelineState::initial(1, 1)
            .agent_chain
            .with_agents(vec!["mock".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(crate::agents::AgentDrain::Fix),
        ..initial_with_locked_permissions(1, 1)
    };

    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::CleanupRequiredFiles { ref files } if files.iter().any(|f| f.contains("fix_result.xml")))
    );
}

#[test]
fn test_fix_chain_emits_extract_fix_result_xml_after_fix_agent_invoked() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: true,
        fix_prompt_prepared_pass: Some(0),
        fix_required_files_cleaned_pass: Some(0),
        fix_agent_invoked_pass: Some(0),
        fix_analysis_agent_invoked_pass: Some(0),
        agent_chain: PipelineState::initial(1, 1)
            .agent_chain
            .with_agents(vec!["mock".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(crate::agents::AgentDrain::Analysis),
        ..initial_with_locked_permissions(1, 1)
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ExtractFixResultXml { pass: 0 }));
}

#[test]
fn test_fix_chain_emits_validate_fix_result_xml_after_extracted() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: true,
        fix_prompt_prepared_pass: Some(0),
        fix_required_files_cleaned_pass: Some(0),
        fix_agent_invoked_pass: Some(0),
        fix_analysis_agent_invoked_pass: Some(0),
        fix_result_xml_extracted_pass: Some(0),
        agent_chain: PipelineState::initial(1, 1)
            .agent_chain
            .with_agents(vec!["mock".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(crate::agents::AgentDrain::Analysis),
        ..initial_with_locked_permissions(1, 1)
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ValidateFixResultXml { pass: 0 }));
}

#[test]
fn test_fix_chain_applies_all_issues_addressed_to_fix_attempt_completed() {
    // Given: fix outcome is complete
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: true,
        fix_prompt_prepared_pass: Some(0),
        fix_required_files_cleaned_pass: Some(0),
        fix_agent_invoked_pass: Some(0),
        fix_analysis_agent_invoked_pass: Some(0),
        fix_result_xml_extracted_pass: Some(0),
        fix_validated_outcome: Some(crate::reducer::state::FixValidatedOutcome {
            pass: 0,
            status: crate::reducer::state::FixStatus::AllIssuesAddressed,
            summary: Some("ok".to_string()),
        }),
        fix_result_xml_archived_pass: Some(0),
        agent_chain: PipelineState::initial(1, 1)
            .agent_chain
            .with_agents(vec!["mock".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(crate::agents::AgentDrain::Analysis),
        ..initial_with_locked_permissions(1, 1)
    };

    // When: orchestration applies fix outcome
    assert!(matches!(
        determine_next_effect(&state),
        Effect::ApplyFixOutcome { pass: 0 }
    ));

    // Then: handler should emit the existing completion event used by reducer today
    // (until fix outcome is fully refactored).
    let handler_event = crate::reducer::mock_effect_handler::MockEffectHandler::new(state)
        .execute_mock(&Effect::ApplyFixOutcome { pass: 0 })
        .event;

    assert!(matches!(
        handler_event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::FixOutcomeApplied { pass: 0 })
    ));
}

#[test]
fn test_fix_chain_emits_archive_fix_result_xml_after_validated() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: true,
        fix_prompt_prepared_pass: Some(0),
        fix_required_files_cleaned_pass: Some(0),
        fix_agent_invoked_pass: Some(0),
        fix_analysis_agent_invoked_pass: Some(0),
        fix_result_xml_extracted_pass: Some(0),
        fix_validated_outcome: Some(crate::reducer::state::FixValidatedOutcome {
            pass: 0,
            status: crate::reducer::state::FixStatus::AllIssuesAddressed,
            summary: None,
        }),
        agent_chain: PipelineState::initial(1, 1)
            .agent_chain
            .with_agents(vec!["mock".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(crate::agents::AgentDrain::Analysis),
        ..initial_with_locked_permissions(1, 1)
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ArchiveFixResultXml { pass: 0 }));
}

#[test]
fn test_fix_chain_emits_apply_fix_outcome_after_fix_result_xml_archived() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: true,
        fix_prompt_prepared_pass: Some(0),
        fix_required_files_cleaned_pass: Some(0),
        fix_agent_invoked_pass: Some(0),
        fix_analysis_agent_invoked_pass: Some(0),
        fix_result_xml_extracted_pass: Some(0),
        fix_validated_outcome: Some(crate::reducer::state::FixValidatedOutcome {
            pass: 0,
            status: crate::reducer::state::FixStatus::AllIssuesAddressed,
            summary: None,
        }),
        fix_result_xml_archived_pass: Some(0),
        agent_chain: PipelineState::initial(1, 1)
            .agent_chain
            .with_agents(vec!["mock".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(crate::agents::AgentDrain::Analysis),
        ..initial_with_locked_permissions(1, 1)
    };

    let effect = determine_next_effect(&state);
    assert!(matches!(effect, Effect::ApplyFixOutcome { pass: 0 }));
}
