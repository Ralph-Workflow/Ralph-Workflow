use super::super::common::TestFixture;
use crate::reducer::boundary::MainEffectHandler;
use crate::reducer::effect::Effect;
use crate::reducer::effect::EffectHandler;
use crate::reducer::event::{ErrorEvent, PipelinePhase, WorkspaceIoErrorKind};
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

#[cfg(debug_assertions)]
#[test]
fn test_create_commit_panics_when_state_is_not_generated() {
    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx();
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.commit = CommitState::NotStarted;

    let mut handler = MainEffectHandler::new(state);
    let panic_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _ = handler.execute(
            Effect::CreateCommit {
                message: "feat: should not run".to_string(),
                files: vec![],
                excluded_files: vec![],
            },
            &mut ctx,
        );
    }));

    assert!(
        panic_result.is_err(),
        "CreateCommit must assert orchestrator ownership invariants before any git side-effects"
    );
}
