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
use ralph_workflow::agents::AgentRole;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{DevelopmentEvent, PipelineEvent};
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::{ContinuationState, PipelineState};
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

/// Test that XSD retry loops are NOT embedded in handlers.
#[test]
fn test_xsd_retry_loops_are_removed() {
    with_default_timeout(|| {
        // XSD retries must be reducer-driven: one failure event at a time,
        // with orchestration deriving the next retry/fallback behavior.
        let mut state = with_locked_prompt_permissions(PipelineState {
            continuation: ContinuationState::new().with_max_xsd_retry(2),
            ..PipelineState::initial(2, 0)
        });

        // Drive to development analysis stage through events.
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(
            state,
            PipelineEvent::agent_chain_initialized(
                AgentRole::Developer.into(),
                vec!["dev-primary".to_string(), "dev-fallback".to_string()],
                3,
                1_000,
                2.0,
                60_000,
            ),
        );
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(state, PipelineEvent::development_context_prepared(0));
        state = reduce(state, PipelineEvent::development_prompt_prepared(0));
        state = reduce(state, PipelineEvent::development_xml_cleaned(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::agent_chain_initialized(
                AgentRole::Analysis.into(),
                vec!["dev-primary".to_string(), "dev-fallback".to_string()],
                3,
                1_000,
                2.0,
                60_000,
            ),
        );
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 }),
        );

        // First failure should keep current agent and derive a retry effect.
        // Clear iteration-start cleanup marker to observe retry effect directly.
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        let next = reduce(
            state,
            PipelineEvent::development_output_validation_failed(0, 0),
        );
        assert_eq!(
            next.agent_chain.current_agent().map(String::as_str),
            Some("dev-primary")
        );
        let effect = determine_next_effect(&next);
        assert!(
            matches!(effect, Effect::InvokeAnalysisAgent { iteration: 0 }),
            "First validation failure should retry analysis agent, got: {effect:?}"
        );

        // Second failure hits retry budget and should advance to fallback agent.
        let exhausted = reduce(
            next,
            PipelineEvent::development_output_validation_failed(0, 0),
        );
        assert_eq!(
            exhausted.agent_chain.current_agent().map(String::as_str),
            Some("dev-fallback")
        );
    });
}

/// Test that Planning output validation retries are reducer-driven.
///
/// Planning must not have hidden validation retry loops in handlers or phase code.
/// Invalid planning output should increment a reducer-visible attempt counter and
/// trigger agent fallback only when the threshold is reached.
#[test]
fn test_planning_output_validation_retries_are_reducer_driven() {
    with_default_timeout(|| {
        let state = with_locked_prompt_permissions(PipelineState {
            continuation: ContinuationState::new().with_max_xsd_retry(2),
            ..PipelineState::initial(2, 0)
        });

        // One failure stays on the same agent and derives a planning retry effect.
        let state = reduce(
            state,
            PipelineEvent::agent_chain_initialized(
                AgentRole::Developer.into(),
                vec!["dev-primary".to_string(), "dev-fallback".to_string()],
                3,
                1_000,
                2.0,
                60_000,
            ),
        );
        let next = reduce(
            state,
            PipelineEvent::planning_output_validation_failed(0, 0),
        );
        assert_eq!(
            next.agent_chain.current_agent().map(String::as_str),
            Some("dev-primary")
        );
        let effect = determine_next_effect(&next);
        assert!(
            matches!(
                effect,
                Effect::PreparePlanningPrompt {
                    iteration: 0,
                    prompt_mode: ralph_workflow::reducer::state::PromptMode::XsdRetry,
                }
            ),
            "Planning validation failure should derive planning XSD retry, got: {effect:?}"
        );

        // Second failure hits retry budget and advances to fallback agent.
        let exhausted = reduce(next, PipelineEvent::planning_output_validation_failed(0, 0));
        assert_eq!(
            exhausted.agent_chain.current_agent().map(String::as_str),
            Some("dev-fallback"),
            "Reducer should advance to fallback agent after max XSD retries"
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

        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::CleanupContinuationContext),
            "Commit transition cleanup must be explicit; got {effect:?}"
        );

        // After explicit cleanup event, commit chain initialization should be required.
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: ralph_workflow::agents::AgentDrain::Commit,
                    ..
                }
            ),
            "Commit phase should require explicit chain initialization; got {effect:?}"
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

/// Test that `.processed` XML files are archive-only.
///
/// This test lives alongside other "no hidden behavior" invariants: the pipeline must
/// not consult `.processed` files as fallback inputs.
#[test]
fn test_processed_xml_files_are_never_used_as_inputs() {
    with_default_timeout(|| {
        use ralph_workflow::files::llm_output_extraction::file_based_extraction::try_extract_from_file_with_workspace;
        use ralph_workflow::workspace::MemoryWorkspace;
        use std::path::Path;

        let workspace = MemoryWorkspace::new_test().with_file(
            ".agent/tmp/development_result.xml.processed",
            "<development>archived</development>",
        );

        // Primary missing, archive present -> must not be used.
        let result = try_extract_from_file_with_workspace(
            &workspace,
            Path::new(".agent/tmp/development_result.xml"),
        );

        assert!(
            result.is_none(),
            "archived .processed XML must not be used as a fallback input"
        );
    });
}
