//! Continuation handling integration tests.
//!
//! Tests verify that continuation-aware prompting works correctly when
//! development iterations return status="partial" or "failed".
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (state transitions, prompt generation)
//! - Tests are deterministic and isolated
//! - Tests behavior, not implementation details

use ralph_workflow::reducer::event::PipelineEvent;
use ralph_workflow::reducer::state::{ContinuationState, DevelopmentStatus, PipelineState};
use ralph_workflow::reducer::state_reduction::reduce;

use crate::test_timeout::with_default_timeout;

// ============================================================================
// ContinuationState Unit Tests
// ============================================================================

/// Test that partial status triggers continuation correctly.
#[test]
fn test_partial_status_triggers_continuation() {
    with_default_timeout(|| {
        let state = PipelineState::initial(5, 2);
        let new_state = reduce(
            state,
            PipelineEvent::development_iteration_continuation_triggered(
                1,
                DevelopmentStatus::Partial,
                "Implemented feature X".to_string(),
                Some(vec!["src/lib.rs".to_string()]),
                Some("Add tests".to_string()),
            ),
        );

        assert!(new_state.continuation.is_continuation());
        assert_eq!(new_state.continuation.continuation_attempt, 1);
        assert_eq!(
            new_state.continuation.previous_status,
            Some(DevelopmentStatus::Partial)
        );
        assert_eq!(
            new_state.continuation.previous_summary,
            Some("Implemented feature X".to_string())
        );
        assert_eq!(
            new_state.continuation.previous_next_steps,
            Some("Add tests".to_string())
        );
        assert_eq!(
            new_state.continuation.previous_files_changed,
            Some(vec!["src/lib.rs".to_string()].into_boxed_slice())
        );
    });
}

/// Test that failed status triggers continuation correctly.
#[test]
fn test_failed_status_triggers_continuation() {
    with_default_timeout(|| {
        let state = PipelineState::initial(5, 2);
        let new_state = reduce(
            state,
            PipelineEvent::development_iteration_continuation_triggered(
                1,
                DevelopmentStatus::Failed,
                "Build failed due to type errors".to_string(),
                None,
                Some("Fix type errors in module X".to_string()),
            ),
        );

        assert!(new_state.continuation.is_continuation());
        assert_eq!(
            new_state.continuation.previous_status,
            Some(DevelopmentStatus::Failed)
        );
        assert!(new_state.continuation.previous_files_changed.is_none());
    });
}

/// Test that completed status resets continuation state.
#[test]
fn test_completed_status_resets_continuation() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(5, 2);
        state.continuation = ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "Previous work".to_string(),
            None,
            None,
        );

        let new_state = reduce(
            state,
            PipelineEvent::development_iteration_continuation_succeeded(1, 2),
        );

        assert!(!new_state.continuation.is_continuation());
        assert_eq!(new_state.continuation.continuation_attempt, 0);
        assert!(new_state.continuation.previous_status.is_none());
    });
}

/// Test that new iteration resets continuation state.
#[test]
fn test_new_iteration_resets_continuation() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(5, 2);
        state.continuation = ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "Previous work".to_string(),
            None,
            None,
        );

        let new_state = reduce(state, PipelineEvent::development_iteration_started(2));

        assert!(!new_state.continuation.is_continuation());
        assert_eq!(new_state.iteration, 2);
    });
}

/// Test that continuation state persists across multiple continuation events.
#[test]
fn test_continuation_state_persists_across_events() {
    with_default_timeout(|| {
        let state = PipelineState::initial(5, 2);

        // Trigger first continuation
        let state = reduce(
            state,
            PipelineEvent::development_iteration_continuation_triggered(
                1,
                DevelopmentStatus::Partial,
                "First attempt".to_string(),
                None,
                None,
            ),
        );
        assert_eq!(state.continuation.continuation_attempt, 1);

        // Trigger second continuation
        let state = reduce(
            state,
            PipelineEvent::development_iteration_continuation_triggered(
                1,
                DevelopmentStatus::Partial,
                "Second attempt".to_string(),
                None,
                None,
            ),
        );

        assert_eq!(state.continuation.continuation_attempt, 2);
        assert_eq!(
            state.continuation.previous_summary,
            Some("Second attempt".to_string())
        );
    });
}

// ============================================================================
// ContinuationState Method Tests
// ============================================================================

/// Test `ContinuationState::new()` creates an empty state.
#[test]
fn test_continuation_state_new() {
    with_default_timeout(|| {
        let state = ContinuationState::new();
        assert!(!state.is_continuation());
        assert_eq!(state.continuation_attempt, 0);
        assert!(state.previous_status.is_none());
        assert!(state.previous_summary.is_none());
        assert!(state.previous_files_changed.is_none());
        assert!(state.previous_next_steps.is_none());
    });
}

/// Test `ContinuationState::trigger_continuation()` increments attempt count.
#[test]
fn test_continuation_state_trigger_increments_attempt() {
    with_default_timeout(|| {
        // ContinuationState::new() uses default max_continue_count = 3
        let state = ContinuationState::new();
        let state =
            state.trigger_continuation(DevelopmentStatus::Partial, "First".to_string(), None, None);
        assert_eq!(state.continuation_attempt, 1);

        let state = state.trigger_continuation(
            DevelopmentStatus::Partial,
            "Second".to_string(),
            None,
            None,
        );
        assert_eq!(state.continuation_attempt, 2);

        // Third call hits defensive check (next_attempt = 3 >= max_continue_count = 3)
        let state =
            state.trigger_continuation(DevelopmentStatus::Partial, "Third".to_string(), None, None);
        assert_eq!(
            state.continuation_attempt, 2,
            "defensive check should prevent increment to 3"
        );
        assert!(
            !state.continue_pending,
            "defensive check should clear continue_pending"
        );
    });
}

/// Test `ContinuationState::reset()` clears all fields.
#[test]
fn test_continuation_state_reset_clears_all() {
    with_default_timeout(|| {
        let state = ContinuationState::new()
            .trigger_continuation(
                DevelopmentStatus::Partial,
                "Work".to_string(),
                Some(vec!["a.rs".to_string()]),
                Some("Next".to_string()),
            )
            .trigger_continuation(
                DevelopmentStatus::Partial,
                "More work".to_string(),
                Some(vec!["b.rs".to_string()]),
                Some("Continue".to_string()),
            );

        assert_eq!(state.continuation_attempt, 2);
        assert!(state.previous_status.is_some());

        let reset = state.reset();
        assert!(!reset.is_continuation());
        assert_eq!(reset.continuation_attempt, 0);
        assert!(reset.previous_status.is_none());
        assert!(reset.previous_summary.is_none());
        assert!(reset.previous_files_changed.is_none());
        assert!(reset.previous_next_steps.is_none());
    });
}

/// Test `DevelopmentStatus` display formatting.
#[test]
fn test_development_status_display() {
    with_default_timeout(|| {
        assert_eq!(format!("{}", DevelopmentStatus::Completed), "completed");
        assert_eq!(format!("{}", DevelopmentStatus::Partial), "partial");
        assert_eq!(format!("{}", DevelopmentStatus::Failed), "failed");
    });
}

// ============================================================================
// Prompt Generation Tests
// ============================================================================

/// Test that continuation prompt is generated with context from previous attempt.
#[test]
fn test_continuation_prompt_includes_previous_context() {
    use ralph_workflow::prompts::prompt_developer_iteration_continuation_xml;
    use ralph_workflow::prompts::template_context::TemplateContext;

    with_default_timeout(|| {
        let template_context = TemplateContext::default();
        let continuation_state = ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "Implemented half the feature".to_string(),
            Some(vec!["src/lib.rs".to_string(), "src/main.rs".to_string()]),
            Some("Add tests for the new functionality".to_string()),
        );

        let workspace = ralph_workflow::workspace::MemoryWorkspace::new_test();
        let prompt = prompt_developer_iteration_continuation_xml(
            &template_context,
            &continuation_state,
            &workspace,
        );

        // Verify the prompt contains key elements
        assert!(
            prompt.contains("IMPLEMENTATION MODE"),
            "Prompt should use implementation-mode framing"
        );
        assert!(
            prompt.contains("CONTINUATION CONTEXT"),
            "Prompt should include continuation context"
        );
        assert!(
            prompt.contains("partial"),
            "Prompt should include previous status"
        );
        assert!(
            prompt.contains("Implemented half the feature"),
            "Prompt should include previous summary"
        );
        assert!(
            prompt.contains("continuation 1 of"),
            "Prompt should include continuation progress label"
        );
        assert!(
            prompt.contains("failed to fully complete the entire plan"),
            "Prompt should frame continuation as incomplete whole-plan completion"
        );
        assert!(
            prompt.contains("going beyond the plan is acceptable")
                || prompt.contains("acceptable to do more than the minimum remaining plan work"),
            "Prompt should explicitly allow going beyond the plan for more complete progress"
        );
        assert!(
            prompt.contains("only recovery-critical information"),
            "Prompt should explicitly restrict continuation context to recovery-critical information"
        );
        assert!(
            !prompt.contains("src/lib.rs") && !prompt.contains("src/main.rs"),
            "Prompt should avoid inlining previous file bookkeeping"
        );
        assert!(
            !prompt.contains("Files changed in previous attempt:"),
            "Prompt should avoid file-list sections in continuation context"
        );
    });
}

/// Test that continuation prompt references original files instead of inlining.
#[test]
fn test_continuation_prompt_references_original_files() {
    use ralph_workflow::prompts::prompt_developer_iteration_continuation_xml;
    use ralph_workflow::prompts::template_context::TemplateContext;

    with_default_timeout(|| {
        let template_context = TemplateContext::default();
        let continuation_state = ContinuationState::new().trigger_continuation(
            DevelopmentStatus::Partial,
            "Did some work".to_string(),
            None,
            None,
        );

        let workspace = ralph_workflow::workspace::MemoryWorkspace::new_test()
            .with_file("PROMPT.md", "Original request")
            .with_file(".agent/PLAN.md", "Implementation plan");
        let prompt = prompt_developer_iteration_continuation_xml(
            &template_context,
            &continuation_state,
            &workspace,
        );

        // Verify inclusion of original request and plan context
        assert!(
            prompt.contains("ORIGINAL REQUEST"),
            "Prompt should include original request section"
        );
        assert!(
            prompt.contains("IMPLEMENTATION PLAN"),
            "Prompt should include implementation plan section"
        );
        assert!(
            prompt.contains("Original request") && prompt.contains("Implementation plan"),
            "Prompt should include original request and plan content"
        );
        assert!(
            prompt.contains("Do NOT create STATUS.md"),
            "Prompt should warn against creating status files"
        );
    });
}

// ============================================================================
// Checkpoint Backward Compatibility Tests
// ============================================================================

/// Test that checkpoints without continuation field can still be loaded.
#[test]
fn test_checkpoint_backward_compatibility_default_continuation() {
    with_default_timeout(|| {
        // The ContinuationState derives Default and has #[serde(default)]
        // This means checkpoints without the continuation field will deserialize correctly
        let state = PipelineState::initial(5, 2);

        // The initial state should have an empty continuation
        assert!(!state.continuation.is_continuation());
        assert_eq!(state.continuation.continuation_attempt, 0);
    });
}
