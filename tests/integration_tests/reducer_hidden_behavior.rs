//! Tests documenting explicit reducer-driven behavior and the absence of hidden paths.
//!
//! These tests act as architectural documentation for the reducer-only pipeline:
//! - No handler-level "helpfulness" (cleanup, fallback, or retry loops)
//! - All retries, fallbacks, and phase transitions are driven by reducer events
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{DevelopmentEvent, PipelineEvent};
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::reducer::state_reduction::reduce;

use ralph_workflow::app::event_loop::{run_event_loop_with_handler, EventLoopConfig};

/// Test that handler cleanup operations are reducer-driven effects, not hidden helpers.
#[test]
fn test_handler_cleanup_requires_effect() {
    with_default_timeout(|| {
        // Cleanup must be driven by explicit effects (e.g., EnsureGitignoreEntries,
        // CleanupContext, CleanupContinuationContext). Handlers must not perform
        // hidden cleanup beyond the effect being executed.
        // Start from a state where cleanup is pending via reducer events.
        let mut state = with_locked_prompt_permissions(PipelineState::initial(2, 1));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: ralph_workflow::reducer::state::DevelopmentStatus::Partial,
                summary: "partial".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationBudgetExhausted {
                iteration: 0,
                total_attempts: 2,
                last_status: ralph_workflow::reducer::state::DevelopmentStatus::Partial,
            }),
        );
        state = reduce(
            state,
            PipelineEvent::development_iteration_completed(0, true),
        );

        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::CleanupContinuationContext),
            "Cleanup must be an explicit effect, got: {effect:?}"
        );
    });
}

/// Test that marker file checks do not influence control flow.
#[test]
fn test_marker_file_check_is_documented_intentional() {
    with_default_timeout(|| {
        // Marker files must not alter phase progression or retry decisions.
        // Only reducer events may change control flow.
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::development_iteration_completed(0, true),
        );

        // Commit-gated design: development_iteration_completed transitions to CommitMessage phase,
        // which requires explicit CleanupContinuationContext before proceeding.
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::CleanupContinuationContext),
            "Commit transition cleanup must be explicit; got {effect:?}"
        );

        // After cleanup, CommitMessage phase initializes the commit agent chain first.
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        let effect = determine_next_effect(&state);
        // Commit-gated design: we're in CommitMessage phase, so drain is Commit (not Review yet).
        // Review phase is reached only after commit completes.
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Commit,
                    ..
                }
            ),
            "After cleanup, CommitMessage phase should initialize Commit agent chain; got {effect:?}"
        );
    });
}

/// Test that the event loop does not inject synthetic checkpoint events.
///
/// Checkpointing must happen only through the `SaveCheckpoint` effect executed by
/// the handler. The event loop must not directly apply `CheckpointSaved` events.
#[test]
fn test_event_loop_does_not_inject_checkpoint_saved_events() {
    with_default_timeout(|| {
        use crate::common::IntegrationFixture;
        use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;

        let mut fixture = IntegrationFixture::new();
        let mut ctx = fixture.ctx(None);

        // Start in FinalValidation so the loop runs without needing SaveCheckpoint.
        let initial_state = with_locked_prompt_permissions(reduce(
            PipelineState::initial(0, 0),
            PipelineEvent::review_phase_completed(false),
        ));
        let mut handler = MockEffectHandler::new(initial_state.clone());

        let loop_config = EventLoopConfig { max_iterations: 10 };

        let _res =
            run_event_loop_with_handler(&mut ctx, Some(initial_state), loop_config, &mut handler)
                .expect("event loop should run");

        // The loop should not inject checkpoint events; without SaveCheckpoint effects,
        // there should be zero CheckpointSaved events applied.
        assert_eq!(
            handler.state.checkpoint_saved_count, 0,
            "event loop must not inject synthetic CheckpointSaved events"
        );
    });
}
