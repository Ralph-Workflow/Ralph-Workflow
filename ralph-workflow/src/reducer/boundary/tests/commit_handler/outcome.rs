use super::super::common::TestFixture;
use crate::reducer::event::{ErrorEvent, WorkspaceIoErrorKind};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::{CommitState, PipelineState};

#[test]
fn test_apply_commit_message_outcome_surfaces_missing_validated_outcome_as_error_event() {
    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx();

    let mut handler = MainEffectHandler::new(PipelineState::initial(1, 0));
    handler.state.commit = CommitState::Generating {
        attempt: 2,
        max_attempts: 3,
    };

    let err = handler
        .apply_commit_message_outcome(&mut ctx)
        .expect_err("apply_commit_message_outcome must surface invariant violations as ErrorEvent");

    let error_event = err
        .downcast_ref::<ErrorEvent>()
        .expect("error should preserve ErrorEvent for event-loop recovery");
    assert!(
        matches!(
            error_event,
            ErrorEvent::ValidatedCommitOutcomeMissing { attempt: 2 }
        ),
        "expected ValidatedCommitOutcomeMissing, got: {error_event:?}"
    );

    // Defensive: ensure we did not produce a stringy 'Other' workspace error.
    assert!(
        !matches!(
            error_event,
            ErrorEvent::WorkspaceReadFailed {
                kind: WorkspaceIoErrorKind::Other,
                ..
            }
        ),
        "expected a specific invariant error, not a generic workspace error"
    );
}
