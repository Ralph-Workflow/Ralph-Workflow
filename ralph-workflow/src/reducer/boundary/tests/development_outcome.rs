use super::common::TestFixture;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{
    ContinuationState, DevelopmentStatus, DevelopmentValidatedOutcome, PipelineState,
};

#[test]
fn test_apply_development_outcome_exhausts_when_next_attempt_reaches_limit() {
    let mut fixture = TestFixture::new();
    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.development_validated_outcome = Some(DevelopmentValidatedOutcome {
        iteration: 0,
        status: DevelopmentStatus::Partial,
        summary: "partial".to_string(),
        files_changed: None,
        next_steps: None,
    });
    handler.state.continuation = ContinuationState {
        continuation_attempt: 2,
        max_continue_count: 3,
        ..ContinuationState::new()
    };

    let mut ctx = fixture.ctx();

    let result = handler
        .apply_development_outcome(&mut ctx, 0)
        .expect("apply_development_outcome should succeed");

    assert!(matches!(
        result.event,
        crate::reducer::event::PipelineEvent::Development(
            crate::reducer::event::DevelopmentEvent::OutcomeApplied { .. }
        )
    ));
}
