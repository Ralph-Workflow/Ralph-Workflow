//! Tests for error handling and recovery in failure completion flow.
//!
//! Verifies robust error handling when things go wrong during failure handling:
//! - Max iterations reached in `AwaitingDevFix` phase
//! - Completion marker write failures
//! - `SaveCheckpoint` panics and errors
//! - Budget exhaustion scenarios

use super::common::{FailingWorkspace, Fixture, SaveBehavior, StalledAwaitingDevFixHandler};
use crate::test_timeout::with_default_timeout;
use ralph_workflow::app::event_loop::{run_event_loop_with_handler, EventLoopConfig};
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::workspace::MemoryWorkspace;
use std::panic;
use std::path::Path;
use std::sync::Arc;

#[test]
fn test_max_iterations_in_awaiting_dev_fix_emits_completion_marker() {
    with_default_timeout(|| {
        // This test validates the defensive completion marker logic when max iterations
        // is reached while in AwaitingDevFix phase. This is the specific bug fix for:
        // "Pipeline exited without completion marker" when max iterations hit during dev-fix.

        let mut fixture = Fixture::new();
        let mut ctx = fixture.ctx();

        // Create a state in AwaitingDevFix phase
        let mut state = PipelineState::initial(1, 1);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.previous_phase = Some(PipelinePhase::Development);

        let mut handler = StalledAwaitingDevFixHandler::new(state.clone(), SaveBehavior::Ok);
        // Set a low max_iterations to trigger the defensive logic
        let max_iterations = 5;
        let config = EventLoopConfig { max_iterations };

        let result = run_event_loop_with_handler(&mut ctx, Some(state), config, &mut handler)
            .expect("Event loop should not error");

        // CRITICAL: Even when hitting max iterations in AwaitingDevFix,
        // the event loop MUST report completion after writing the marker
        assert!(
            result.completed,
            "BUG: Event loop hit max iterations in AwaitingDevFix and exited without completion. \
             The defensive completion marker logic should have forced completion. \
             final_phase={:?}, events_processed={}, checkpoint_saved_count={}",
            result.final_phase, result.events_processed, handler.state.checkpoint_saved_count
        );

        // Verify we transitioned to Interrupted
        assert_eq!(
            result.final_phase,
            PipelinePhase::Interrupted,
            "Should have forced transition to Interrupted when max iterations hit in AwaitingDevFix"
        );

        // Verify checkpoint_saved_count was incremented to satisfy is_complete()
        assert!(
            handler.state.checkpoint_saved_count > 0,
            "checkpoint_saved_count should be incremented after forced completion"
        );

        assert!(
            handler.save_attempts > 0,
            "SaveCheckpoint should be attempted after forced completion"
        );

        // Verify completion marker was written
        let marker_path = Path::new(".agent/tmp/completion_marker");
        assert!(
            fixture.workspace.exists(marker_path),
            "Completion marker must be written when max iterations hit in AwaitingDevFix"
        );

        let marker_content = fixture
            .workspace
            .read(marker_path)
            .expect("Should read completion marker");
        assert!(
            marker_content.contains("failure"),
            "Completion marker should indicate failure"
        );
        assert!(
            result.events_processed >= max_iterations,
            "Event loop should reach max iterations to exercise forced completion"
        );
    });
}

#[test]
fn test_forced_completion_does_not_report_complete_when_marker_write_fails() {
    with_default_timeout(|| {
        let failing_workspace = Arc::new(FailingWorkspace::new(MemoryWorkspace::new_test(), true));
        let mut fixture = Fixture::with_workspace(failing_workspace);
        let mut ctx = fixture.ctx();

        let mut state = PipelineState::initial(1, 1);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.previous_phase = Some(PipelinePhase::Development);

        let mut handler = StalledAwaitingDevFixHandler::new(state.clone(), SaveBehavior::Ok);
        let config = EventLoopConfig { max_iterations: 3 };

        let result = run_event_loop_with_handler(&mut ctx, Some(state), config, &mut handler)
            .expect("Event loop should not error");

        assert!(
            !result.completed,
            "Event loop must not report completion when completion marker cannot be written"
        );
        assert_eq!(
            result.final_phase,
            PipelinePhase::AwaitingDevFix,
            "Should remain in AwaitingDevFix when completion marker write fails"
        );

        let marker_path = Path::new(".agent/tmp/completion_marker");
        assert!(
            !fixture.workspace.exists(marker_path),
            "Completion marker should not exist when write fails"
        );
    });
}

#[test]
fn test_forced_completion_catches_save_checkpoint_panic() {
    with_default_timeout(|| {
        let mut fixture = Fixture::new();
        let mut ctx = fixture.ctx();

        let mut state = PipelineState::initial(1, 1);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.previous_phase = Some(PipelinePhase::Development);

        let mut handler = StalledAwaitingDevFixHandler::new(state.clone(), SaveBehavior::Panic);
        let config = EventLoopConfig { max_iterations: 3 };

        let result = panic::catch_unwind(panic::AssertUnwindSafe(|| {
            run_event_loop_with_handler(&mut ctx, Some(state), config, &mut handler)
        }));

        assert!(
            result.is_ok(),
            "SaveCheckpoint panic should be caught by event loop"
        );

        let loop_result = result.expect("Expected event loop result");
        assert!(
            loop_result.is_ok(),
            "Event loop should return Ok result when handling panics"
        );

        let loop_result = loop_result.expect("Expected event loop result");
        assert!(
            loop_result.completed,
            "Event loop should terminate cleanly after SaveCheckpoint panic"
        );
        assert_eq!(
            loop_result.final_phase,
            PipelinePhase::Interrupted,
            "SaveCheckpoint panic recovery should terminate in Interrupted phase"
        );
    });
}

#[test]
fn test_forced_completion_reduces_save_checkpoint_error_event() {
    with_default_timeout(|| {
        let mut fixture = Fixture::new();
        let mut ctx = fixture.ctx();

        let mut state = PipelineState::initial(1, 1);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.previous_phase = Some(PipelinePhase::Development);

        let mut handler =
            StalledAwaitingDevFixHandler::new(state.clone(), SaveBehavior::ErrorEvent);
        let max_iterations = 3;
        let config = EventLoopConfig { max_iterations };

        let result = run_event_loop_with_handler(&mut ctx, Some(state), config, &mut handler)
            .expect("Event loop should not error");

        assert!(
            result.events_processed >= max_iterations,
            "Event loop should hit max iterations to exercise forced completion"
        );
        assert_eq!(
            handler.state.previous_phase,
            Some(PipelinePhase::Interrupted),
            "SaveCheckpoint error event should be reduced through the reducer"
        );
    });
}

#[test]
fn test_budget_exhaustion_continues_to_commit_not_terminate() {
    with_default_timeout(|| {
        // This test verifies that when dev-fix budget is exhausted (simulated by
        // hitting max iterations in AwaitingDevFix), the pipeline advances to
        // commit/finalization phase instead of terminating early.

        let mut fixture = Fixture::new();
        let mut ctx = fixture.ctx();

        let mut state = PipelineState::initial(1, 1);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.previous_phase = Some(PipelinePhase::Development);

        let mut handler = StalledAwaitingDevFixHandler::new(state.clone(), SaveBehavior::Ok);
        let config = EventLoopConfig { max_iterations: 5 };

        let result = run_event_loop_with_handler(&mut ctx, Some(state), config, &mut handler)
            .expect("Event loop should complete");

        // Pipeline should complete (transition to Interrupted, then checkpoint saved)
        assert!(
            result.completed,
            "Pipeline must complete after budget exhaustion, not terminate early"
        );

        // Verify completion marker was written
        assert!(
            fixture
                .workspace
                .exists(Path::new(".agent/tmp/completion_marker")),
            "Completion marker must be written even when budget exhausted"
        );

        // Verify we're in Interrupted phase (ready for commit/finalization)
        assert_eq!(
            result.final_phase,
            PipelinePhase::Interrupted,
            "Budget exhaustion should transition to Interrupted for commit/finalization"
        );
    });
}
