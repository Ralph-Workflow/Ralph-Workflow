use super::common::TestFixture;
use crate::reducer::event::PipelineEvent;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{FixStatus, FixValidatedOutcome, PipelineState};

#[test]
fn test_apply_fix_outcome_emits_fix_continuation_triggered_for_issues_remain() {
    let mut fixture = TestFixture::new();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.fix_validated_outcome = Some(FixValidatedOutcome {
        pass: 0,
        status: FixStatus::IssuesRemain,
        summary: Some("needs more".to_string()),
    });

    let mut ctx = fixture.ctx();

    let result = handler
        .apply_fix_outcome(&mut ctx, 0)
        .expect("apply_fix_outcome should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::FixOutcomeApplied { pass: 0 })
    ));
}

#[test]
fn test_apply_fix_outcome_emits_fix_attempt_completed_for_all_issues_addressed() {
    let mut fixture = TestFixture::new();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.fix_validated_outcome = Some(FixValidatedOutcome {
        pass: 0,
        status: FixStatus::AllIssuesAddressed,
        summary: Some("done".to_string()),
    });

    let mut ctx = fixture.ctx();

    let result = handler
        .apply_fix_outcome(&mut ctx, 0)
        .expect("apply_fix_outcome should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::FixOutcomeApplied { pass: 0 })
    ));
}

#[test]
fn test_apply_fix_outcome_emits_fix_continuation_triggered_for_failed() {
    let mut fixture = TestFixture::new();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.fix_validated_outcome = Some(FixValidatedOutcome {
        pass: 0,
        status: FixStatus::Failed,
        summary: Some("blocked".to_string()),
    });

    let mut ctx = fixture.ctx();

    let result = handler
        .apply_fix_outcome(&mut ctx, 0)
        .expect("apply_fix_outcome should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::FixOutcomeApplied { pass: 0 })
    ));
}

#[test]
fn test_apply_fix_outcome_emits_fix_continuation_budget_exhausted_when_limit_reached() {
    let mut fixture = TestFixture::new();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
    handler.state.continuation.max_fix_continue_count = 3;
    handler.state.continuation.fix_continuation_attempt = 2;
    handler.state.fix_validated_outcome = Some(FixValidatedOutcome {
        pass: 0,
        status: FixStatus::IssuesRemain,
        summary: Some("still failing".to_string()),
    });

    let mut ctx = fixture.ctx();

    let result = handler
        .apply_fix_outcome(&mut ctx, 0)
        .expect("apply_fix_outcome should succeed");

    assert!(matches!(
        result.event,
        PipelineEvent::Review(crate::reducer::event::ReviewEvent::FixOutcomeApplied { pass: 0 })
    ));
}

#[test]
fn test_apply_fix_outcome_returns_error_when_missing_outcome() {
    let mut fixture = TestFixture::new();

    let handler = MainEffectHandler::new(PipelineState::initial(1, 1));

    let mut ctx = fixture.ctx();

    let err = handler
        .apply_fix_outcome(&mut ctx, 0)
        .expect_err("apply_fix_outcome should return error when fix outcome is missing");

    assert!(
        err.to_string().contains("Missing validated fix outcome"),
        "Expected error about missing validated fix outcome, got: {err}"
    );
}
