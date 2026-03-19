//! Agent chain fallback tests after XSD retry exhaustion
//! Tests agent chain advancement when XSD retries are exhausted

use super::*;

#[test]
fn test_review_output_validation_failed_resets_agent_invoked_pass() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        review_agent_invoked_pass: Some(0), // Agent was invoked
        review_issues_xml_extracted_pass: None, // Extraction not done yet
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::review_output_validation_failed(0, 0, None),
    );

    // After validation failure, agent invocation should be reset so orchestration
    // can re-invoke the agent with the XSD retry prompt
    assert!(
        new_state.review_agent_invoked_pass.is_none(),
        "review_agent_invoked_pass should be reset after validation failure, got {:?}",
        new_state.review_agent_invoked_pass
    );
}

/// Test that review issues.xml missing resets agent invocation state
/// so the agent gets re-invoked with the XSD retry prompt.
#[test]
fn test_review_issues_xml_missing_resets_agent_invoked_pass() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        review_agent_invoked_pass: Some(0), // Agent was invoked
        review_issues_xml_extracted_pass: None,
        ..create_test_state()
    };

    let new_state = reduce(state, PipelineEvent::review_issues_xml_missing(0, 0, None));

    // After missing XML is detected, agent invocation should be reset so orchestration
    // can re-invoke the agent with the XSD retry prompt
    assert!(
        new_state.review_agent_invoked_pass.is_none(),
        "review_agent_invoked_pass should be reset after issues.xml missing, got {:?}",
        new_state.review_agent_invoked_pass
    );
}

/// Test that fix output validation failure resets agent invocation state
/// so the agent gets re-invoked with the XSD retry prompt.
#[test]
fn test_fix_output_validation_failed_resets_agent_invoked_pass() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        review_issues_found: true,       // Indicates we're in fix mode
        fix_agent_invoked_pass: Some(0), // Agent was invoked
        fix_result_xml_extracted_pass: None,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::fix_output_validation_failed(0, 0, None),
    );

    // After validation failure, agent invocation should be reset so orchestration
    // can re-invoke the agent with the XSD retry prompt
    assert!(
        new_state.fix_agent_invoked_pass.is_none(),
        "fix_agent_invoked_pass should be reset after validation failure, got {:?}",
        new_state.fix_agent_invoked_pass
    );
}

/// Test that fix result.xml missing resets agent invocation state
/// so the agent gets re-invoked with the XSD retry prompt.
#[test]
fn test_fix_result_xml_missing_resets_agent_invoked_pass() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        review_issues_found: true,       // Indicates we're in fix mode
        fix_agent_invoked_pass: Some(0), // Agent was invoked
        fix_result_xml_extracted_pass: None,
        ..create_test_state()
    };

    let new_state = reduce(state, PipelineEvent::fix_result_xml_missing(0, 0, None));

    // After missing XML is detected, agent invocation should be reset so orchestration
    // can re-invoke the agent with the XSD retry prompt
    assert!(
        new_state.fix_agent_invoked_pass.is_none(),
        "fix_agent_invoked_pass should be reset after fix_result.xml missing, got {:?}",
        new_state.fix_agent_invoked_pass
    );
}

// =========================================================================
// Planning XSD retry orchestration reset tests
// =========================================================================

/// Test that planning output validation failure resets agent invocation state
/// so the agent gets re-invoked with the XSD retry prompt.
#[test]
fn test_planning_output_validation_failed_resets_agent_invoked_iteration() {
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        iteration: 0,
        planning_agent_invoked_iteration: Some(0), // Agent was invoked
        planning_xml_extracted_iteration: None,    // Extraction not done yet
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::planning_output_validation_failed(0, 0),
    );

    // After validation failure, agent invocation should be reset so orchestration
    // can re-invoke the agent with the XSD retry prompt
    assert!(
        new_state.planning_agent_invoked_iteration.is_none(),
        "planning_agent_invoked_iteration should be reset after validation failure, got {:?}",
        new_state.planning_agent_invoked_iteration
    );
    assert!(
        new_state.planning_prompt_prepared_iteration.is_none(),
        "planning_prompt_prepared_iteration should be reset for XSD retry prompt preparation"
    );
}

/// Test that plan.xml missing resets agent invocation state
/// so the agent gets re-invoked with the XSD retry prompt.
#[test]
fn test_planning_plan_xml_missing_resets_agent_invoked_iteration() {
    let state = PipelineState {
        phase: PipelinePhase::Planning,
        iteration: 0,
        planning_agent_invoked_iteration: Some(0), // Agent was invoked
        planning_xml_extracted_iteration: None,
        ..create_test_state()
    };

    let new_state = reduce(state, PipelineEvent::planning_xml_missing(0, 0));

    // After missing XML is detected, agent invocation should be reset so orchestration
    // can re-invoke the agent with the XSD retry prompt
    assert!(
        new_state.planning_agent_invoked_iteration.is_none(),
        "planning_agent_invoked_iteration should be reset after plan.xml missing, got {:?}",
        new_state.planning_agent_invoked_iteration
    );
}

// =========================================================================
// Development XSD retry orchestration reset tests
// =========================================================================

/// Test that development output validation failure resets analysis agent invocation
/// so the analysis agent gets re-invoked with the XSD retry.
///
/// Note: Development XSD retry is for the ANALYSIS agent output, not the developer agent.
/// So we preserve developer progress and only reset `analysis_agent_invoked_iteration`.
#[test]
fn test_development_output_validation_failed_resets_analysis_agent_invoked() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        development_agent_invoked_iteration: Some(1), // Developer was invoked
        analysis_agent_invoked_iteration: Some(1),    // Analysis was invoked
        development_xml_extracted_iteration: None,    // Extraction not done yet
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::development_output_validation_failed(1, 0),
    );

    // Developer progress should be preserved
    assert_eq!(
        new_state.development_agent_invoked_iteration,
        Some(1),
        "development_agent_invoked_iteration should be preserved (XSD retry is for analysis)"
    );
    // Analysis agent should be reset for retry
    assert!(
        new_state.analysis_agent_invoked_iteration.is_none(),
        "analysis_agent_invoked_iteration should be reset for XSD retry, got {:?}",
        new_state.analysis_agent_invoked_iteration
    );
}

/// Test that `development_result.xml` missing resets analysis agent invocation.
#[test]
fn test_development_xml_missing_resets_analysis_agent_invoked() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        development_agent_invoked_iteration: Some(1),
        analysis_agent_invoked_iteration: Some(1),
        development_xml_extracted_iteration: None,
        ..create_test_state()
    };

    let new_state = reduce(state, PipelineEvent::development_xml_missing(1, 0));

    // Developer progress should be preserved
    assert_eq!(
        new_state.development_agent_invoked_iteration,
        Some(1),
        "development_agent_invoked_iteration should be preserved"
    );
    // Analysis agent should be reset for retry
    assert!(
        new_state.analysis_agent_invoked_iteration.is_none(),
        "analysis_agent_invoked_iteration should be reset after xml missing, got {:?}",
        new_state.analysis_agent_invoked_iteration
    );
}

// =========================================================================
// Commit XSD retry orchestration reset tests
// =========================================================================

/// Test that commit message validation failure resets agent invocation state
/// so the agent gets re-invoked with the XSD retry prompt.
#[test]
fn test_commit_message_validation_failed_resets_agent_invoked() {
    let state = PipelineState {
        phase: PipelinePhase::CommitMessage,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_agent_invoked: true, // Agent was invoked
        commit_xml_extracted: false,
        ..create_test_state()
    };

    let new_state = reduce(
        state,
        PipelineEvent::commit_message_validation_failed("Invalid XML".to_string(), 1),
    );

    // After validation failure, agent invocation should be reset so orchestration
    // can re-invoke the agent with the XSD retry prompt
    assert!(
        !new_state.commit_agent_invoked,
        "commit_agent_invoked should be reset after validation failure"
    );
    assert!(
        !new_state.commit_prompt_prepared,
        "commit_prompt_prepared should be reset for XSD retry prompt preparation"
    );
}
