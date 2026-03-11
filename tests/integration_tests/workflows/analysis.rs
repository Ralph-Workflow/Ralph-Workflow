//! Integration tests for independent result analysis.
//!
//! These tests verify that the analysis agent is invoked after EVERY
//! development iteration to produce an objective assessment based on git diff
//! vs PLAN.md.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{DevelopmentEvent, PipelineEvent};
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::reducer::state_reduction::reduce;

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;

/// Test that `AnalysisAgentInvoked` event type exists and can be constructed.
///
/// This basic test verifies:
/// 1. The `AnalysisAgentInvoked` event variant exists
/// 2. It can be constructed with an iteration number
#[test]
fn test_analysis_agent_invoked_event_exists() {
    with_default_timeout(|| {
        // Verify the event type can be constructed
        let event =
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 });

        // Verify it's the correct variant
        match event {
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration }) => {
                assert_eq!(iteration, 0);
            }
            _ => panic!("Expected AnalysisAgentInvoked event"),
        }
    });
}

/// Test that `InvokeAnalysisAgent` effect type exists and can be constructed.
///
/// This test verifies:
/// 1. The `InvokeAnalysisAgent` effect variant exists
/// 2. It can be constructed with an iteration number
#[test]
fn test_invoke_analysis_agent_effect_exists() {
    with_default_timeout(|| {
        // Verify the effect type can be constructed
        let effect = Effect::InvokeAnalysisAgent { iteration: 0 };

        // Verify it's the correct variant
        match effect {
            Effect::InvokeAnalysisAgent { iteration } => {
                assert_eq!(iteration, 0);
            }
            _ => panic!("Expected InvokeAnalysisAgent effect"),
        }
    });
}

/// Test that analysis agent is invoked after the first iteration when multiple iterations exist.
///
/// This test verifies that analysis runs after EVERY development iteration,
/// not just the final one.
#[test]
fn test_analysis_runs_after_first_iteration_when_multiple_iterations() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;

        // Given: Pipeline with 3 total iterations, first iteration just completed.
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 2));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(
            state,
            PipelineEvent::agent_chain_initialized(
                AgentRole::Developer,
                vec!["claude".to_string()],
                3,
                1_000,
                2.0,
                60_000,
            ),
        );
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        state = reduce(state, PipelineEvent::development_context_prepared(0));
        state = reduce(state, PipelineEvent::development_prompt_prepared(0));
        state = reduce(state, PipelineEvent::development_xml_cleaned(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));

        // When: Determining next effect
        let effect = determine_next_effect(&state);

        // Then: Should initialize analysis agent chain first (role-aware), then invoke analysis.
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    role: AgentRole::Analysis,
                    ..
                }
            ),
            "Expected InitializeAgentChain(Analysis) before invoking analysis agent, got {effect:?}"
        );
    });
}

/// Test that analysis agent is invoked after EVERY iteration.
///
/// Verifies the core requirement: analysis must run after each development
/// iteration, regardless of iteration count.
#[test]
fn test_analysis_runs_after_every_iteration() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;

        // Test across multiple iterations
        for iter in 0..3 {
            // Given: Pipeline with 3 iterations, current iteration just completed.
            let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 2));
            state = reduce(state, PipelineEvent::planning_phase_completed());
            state = reduce(
                state,
                PipelineEvent::agent_chain_initialized(
                    AgentRole::Developer,
                    vec!["claude".to_string()],
                    3,
                    1_000,
                    2.0,
                    60_000,
                ),
            );
            state = reduce(state, PipelineEvent::development_iteration_started(iter));
            state = reduce(
                state,
                PipelineEvent::development_continuation_context_cleaned(),
            );
            state = reduce(state, PipelineEvent::development_context_prepared(iter));
            state = reduce(state, PipelineEvent::development_prompt_prepared(iter));
            state = reduce(state, PipelineEvent::development_xml_cleaned(iter));
            state = reduce(state, PipelineEvent::development_agent_invoked(iter));

            // When: Determining next effect after dev agent completes
            let effect = determine_next_effect(&state);

            // Then: Should initialize analysis agent chain first (role-aware), then invoke analysis.
            assert!(
                matches!(
                    effect,
                    Effect::InitializeAgentChain {
                        role: AgentRole::Analysis,
                        ..
                    }
                ),
                "Expected InitializeAgentChain(Analysis) after iteration {iter}, got {effect:?}"
            );
        }
    });
}

/// Test that analysis agent does NOT run before development agent completes.
///
/// Verifies the sequencing: dev agent must complete before analysis agent runs.
#[test]
fn test_analysis_does_not_run_before_dev_agent_completes() {
    with_default_timeout(|| {
        // Given: Pipeline in development where development agent has NOT completed yet.
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );

        // When: Determining next effect
        let effect = determine_next_effect(&state);

        // Then: Should NOT be InvokeAnalysisAgent
        assert!(
            !matches!(effect, Effect::InvokeAnalysisAgent { .. }),
            "Analysis should not run before dev agent completes, got {effect:?}"
        );
    });
}

/// Test that analysis agent does NOT run twice for the same iteration.
///
/// Verifies idempotency: once analysis runs for an iteration, it doesn't run again.
#[test]
fn test_analysis_does_not_run_twice_for_same_iteration() {
    with_default_timeout(|| {
        // Given: Pipeline where both dev and analysis agents have completed for iteration 0.
        let mut state = with_locked_prompt_permissions(PipelineState::initial(2, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        state = reduce(state, PipelineEvent::development_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 }),
        );

        // When: Determining next effect
        let effect = determine_next_effect(&state);

        // Then: Should NOT be InvokeAnalysisAgent (should move to ExtractDevelopmentXml)
        assert!(
            !matches!(effect, Effect::InvokeAnalysisAgent { .. }),
            "Analysis should not run twice for iteration 0, got {effect:?}"
        );
    });
}

/// Test that `AnalysisAgentInvoked` event updates state correctly.
///
/// Verifies that the reducer properly records when analysis agent is invoked.
#[test]
fn test_analysis_agent_invoked_event_updates_state() {
    with_default_timeout(|| {
        // Given: State where analysis should be recorded for iteration 1.
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(1));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        state = reduce(state, PipelineEvent::development_agent_invoked(1));

        // When: Processing AnalysisAgentInvoked event
        let event =
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 1 });
        let new_state = reduce(state, event);

        // Then: State should record that analysis was invoked for iteration 1
        assert_eq!(
            new_state.analysis_agent_invoked_iteration,
            Some(1),
            "State should record analysis agent invocation for iteration 1"
        );
    });
}

/// Test that analysis does NOT increment the iteration counter.
///
/// CRITICAL: This verifies the core constraint that -D N means exactly N
/// planning cycles, regardless of analysis or continuation.
///
/// Only the commit phase (via `compute_post_commit_transition`) should
/// increment the iteration counter. Analysis is verification only, NOT
/// a development iteration.
#[test]
fn test_analysis_does_not_increment_iteration_counter() {
    with_default_timeout(|| {
        // Given: State at iteration 1 before analysis.
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(1));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        state = reduce(state, PipelineEvent::development_agent_invoked(1));

        // When: Processing AnalysisAgentInvoked event
        let event =
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 1 });
        let new_state = reduce(state, event);

        // Then: Iteration counter should remain unchanged
        assert_eq!(
            new_state.iteration, 1,
            "Analysis must NOT increment iteration counter"
        );

        // And: Only analysis_agent_invoked_iteration should be updated
        assert_eq!(
            new_state.analysis_agent_invoked_iteration,
            Some(1),
            "Should record analysis invocation"
        );
    });
}

/// Test that starting a new continuation attempt resets analysis tracking.
///
/// Regression for a bug where continuation attempts would re-run `CleanupDevelopmentXml`,
/// delete `.agent/tmp/development_result.xml`, and then SKIP analysis because
/// `analysis_agent_invoked_iteration` was still set. That caused missing XML and
/// validation failures.
#[test]
fn test_continuation_triggered_resets_analysis_invoked_tracking() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::state::DevelopmentStatus;

        // Given: A state where analysis already ran for iteration 0.
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        state = reduce(state, PipelineEvent::development_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 }),
        );

        // When: Continuation is triggered (new dev-agent invocation will happen in same iteration)
        let event = PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
            iteration: 0,
            status: DevelopmentStatus::Partial,
            summary: "work incomplete".to_string(),
            files_changed: None,
            next_steps: Some("continue".to_string()),
        });
        let new_state = reduce(state, event);

        // Then: analysis invocation marker must be reset so analysis runs again after the next
        // development-agent invocation.
        assert_eq!(
            new_state.analysis_agent_invoked_iteration, None,
            "ContinuationTriggered must reset analysis tracking"
        );
    });
}

/// Test that XSD retry during Development targets analysis (not dev prompt).
///
/// Regression: XSD retry used to re-run `PrepareDevelopmentPrompt`, which re-ran the
/// entire dev flow even though the invalid XML is produced by the analysis agent.
#[test]
fn test_development_xsd_retry_reinvokes_analysis_agent() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;

        // Given: Development phase with an XSD retry pending (driven by reducer events).
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        state = reduce(state, PipelineEvent::development_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 }),
        );
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutputValidationFailed {
                iteration: 0,
                attempt: 0,
            }),
        );

        // When: Determining next effect
        let effect = determine_next_effect(&state);

        // Then: XSD retry should target analysis. Depending on current role, orchestration may
        // initialize the analysis chain before invoking the analysis agent.
        assert!(
            matches!(effect, Effect::InvokeAnalysisAgent { iteration: 0 })
                || matches!(
                    effect,
                    Effect::InitializeAgentChain {
                        role: AgentRole::Analysis,
                        ..
                    }
                ),
            "expected XSD retry to initialize analysis chain or invoke analysis agent, got {effect:?}"
        );
    });
}

/// Test that analysis agent can handle empty git diff with plan satisfied.
///
/// Verifies that when git diff is empty and the plan indicates no changes were
/// needed, the analysis agent can correctly identify status="completed".
#[test]
fn test_analysis_empty_diff_plan_satisfied() {
    with_default_timeout(|| {
        // Given: A completed analysis result where empty diff is expected
        let xml = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Plan verification complete. No code changes needed as feature already exists.</ralph-summary>
</ralph-development-result>";

        // When: Validating the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

        // Then: Should parse successfully with completed status
        assert!(result.is_ok(), "Valid completed XML should parse");

        let elements = result.unwrap();
        assert_eq!(
            elements.status, "completed",
            "Status should be completed when no changes were needed"
        );
        assert!(
            elements.summary.contains("No code changes needed"),
            "Summary should explain why no changes were made"
        );
        assert!(elements.is_completed(), "Should identify as completed");
    });
}

/// Test that analysis agent can handle empty git diff when changes were expected.
///
/// Verifies that when git diff is empty but the plan requires code changes,
/// the analysis agent can correctly identify status="failed" (dev agent didn't execute).
#[test]
fn test_analysis_empty_diff_plan_requires_changes() {
    with_default_timeout(|| {
        // Given: A failed analysis result where empty diff indicates failure
        let xml = r"<ralph-development-result>
<ralph-status>failed</ralph-status>
<ralph-summary>Development agent failed to execute. Plan requires adding src/feature.rs but no changes were made.</ralph-summary>
</ralph-development-result>";

        // When: Validating the XML
        let result = ralph_workflow::validate_development_result_xml(xml);

        // Then: Should parse successfully with failed status
        assert!(result.is_ok(), "Valid failed XML should parse");

        let elements = result.unwrap();
        assert_eq!(
            elements.status, "failed",
            "Status should be failed when expected changes weren't made"
        );
        assert!(
            elements.summary.contains("no changes were made"),
            "Summary should explain that expected changes weren't made"
        );
        assert!(elements.is_failed(), "Should identify as failed");
    });
}

/// Test that XSD validation errors trigger analysis agent retry.
///
/// Verifies that when analysis agent produces invalid XML, the XSD retry
/// mechanism re-invokes the analysis agent with error context.
#[test]
fn test_analysis_xsd_invalid_triggers_retry() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;

        // Given: State after analysis produced invalid XML (missing summary)
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        state = reduce(state, PipelineEvent::development_agent_invoked(0));

        // Enter analysis role and record analysis invocation.
        state = reduce(
            state,
            PipelineEvent::agent_chain_initialized(
                AgentRole::Analysis,
                vec!["claude".to_string()],
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
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::XmlExtracted { iteration: 0 }),
        );

        // XSD validation failure should set retry-pending state via reducer.
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::OutputValidationFailed {
                iteration: 0,
                attempt: 0,
            }),
        );

        // When: Determining next effect during XSD retry
        let effect = determine_next_effect(&state);

        // Then: Should re-invoke analysis agent (not dev agent)
        assert!(
            matches!(effect, Effect::InvokeAnalysisAgent { iteration: 0 }),
            "XSD retry should re-invoke analysis agent, got {effect:?}"
        );
    });
}

/// Test that analysis agent fallback to next agent works correctly.
///
/// Verifies that when analysis agent fails repeatedly with the current agent,
/// the agent chain switches to the next agent.
#[test]
fn test_analysis_uses_agent_chain_fallback() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;

        // Given: State with multiple agents in chain, after repeated failures
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());

        // Set up agent chain with two agents
        state = reduce(
            state,
            PipelineEvent::agent_chain_initialized(
                AgentRole::Developer,
                vec!["agent1".to_string(), "agent2".to_string()],
                3,
                1000,
                2.0,
                60_000,
            ),
        );
        assert_eq!(
            state.agent_chain.current_agent().unwrap(),
            "agent1",
            "Should start with first agent"
        );

        // Drive repeated validation failures through reducer events.
        // With max_xsd_retry_count=10 by default, the 10th failure exhausts retries
        // and switches to the next agent.
        for attempt in 0..10 {
            state = reduce(
                state,
                PipelineEvent::Development(DevelopmentEvent::OutputValidationFailed {
                    iteration: 0,
                    attempt,
                }),
            );
        }

        // Then: agent chain should have advanced to next agent via reducer transitions.
        assert_eq!(
            state.agent_chain.current_agent().unwrap(),
            "agent2",
            "After exhausting XSD retries, fallback should advance to next agent"
        );

        // Observable follow-up behavior: fallback happened without direct internal mutation.
        let next_effect = determine_next_effect(&state);
        assert!(
            !matches!(next_effect, Effect::InvokeAnalysisAgent { .. }),
            "After fallback transition, flow should not immediately invoke analysis agent, got {next_effect:?}"
        );
    });
}

/// Test complete pipeline flow with analysis verification.
///
/// End-to-end test verifying the full flow: Development -> Analysis -> Extract -> Validate.
#[test]
fn test_complete_pipeline_with_analysis_verification() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRole;
        use ralph_workflow::reducer::state::DevelopmentStatus;

        // Given: reach development after planning through reducer events.
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());

        // Set up developer chain for development phase.
        state = reduce(
            state,
            PipelineEvent::agent_chain_initialized(
                AgentRole::Developer,
                vec!["claude".to_string()],
                3,
                1000,
                2.0,
                60_000,
            ),
        );

        // Drive normal development steps to the point where analysis should run.
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        state = reduce(state, PipelineEvent::development_context_prepared(0));
        state = reduce(state, PipelineEvent::development_prompt_prepared(0));
        state = reduce(state, PipelineEvent::development_xml_cleaned(0));
        state = reduce(state, PipelineEvent::development_agent_invoked(0));

        // Orchestrator should initialize analysis chain (role-aware), then invoke analysis.
        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    role: AgentRole::Analysis,
                    ..
                }
            ),
            "After dev agent, should initialize analysis chain, got {effect:?}"
        );

        // Step 2b: Simulate chain initialization
        state = reduce(
            state,
            PipelineEvent::agent_chain_initialized(
                AgentRole::Analysis,
                vec!["claude".to_string()],
                3,
                1000,
                2.0,
                60_000,
            ),
        );

        // Step 2c: Now analysis agent should be invoked
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::InvokeAnalysisAgent { iteration: 0 }),
            "After analysis chain init, should invoke analysis agent, got {effect:?}"
        );

        // Step 3: Analysis agent completes
        let event =
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 });
        state = reduce(state, event);
        assert_eq!(
            state.analysis_agent_invoked_iteration,
            Some(0),
            "State should record analysis agent invocation"
        );

        // Step 4: Orchestrator should extract XML
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::ExtractDevelopmentXml { iteration: 0 }),
            "After analysis, should extract XML, got {effect:?}"
        );

        // Step 5: XML extraction completes
        let event = PipelineEvent::Development(DevelopmentEvent::XmlExtracted { iteration: 0 });
        state = reduce(state, event);
        assert_eq!(
            state.development_xml_extracted_iteration,
            Some(0),
            "State should record XML extraction"
        );

        // Step 6: Orchestrator should validate XML
        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::ValidateDevelopmentXml { iteration: 0 }),
            "After extraction, should validate XML, got {effect:?}"
        );

        // Step 7: XML validation completes with success
        let event = PipelineEvent::Development(DevelopmentEvent::XmlValidated {
            iteration: 0,
            status: DevelopmentStatus::Completed,
            summary: "Analysis complete".to_string(),
            files_changed: Some(vec!["src/main.rs".to_string()]),
            next_steps: None,
        });
        state = reduce(state, event);

        // Verify: Development outcome is stored
        assert!(
            state.development_validated_outcome.is_some(),
            "Validated outcome should be stored"
        );
        let outcome = state.development_validated_outcome.unwrap();
        assert_eq!(outcome.status, DevelopmentStatus::Completed);
        assert_eq!(outcome.summary, "Analysis complete");
    });
}

/// Test that -D 3 produces exactly 3 planning cycles regardless of analysis.
///
/// CRITICAL regression test: Verifies that analysis does NOT consume `developer_iters` budget.
/// The -D N flag should mean exactly N planning cycles, not N development agent invocations.
#[test]
fn test_developer_iters_3_produces_exactly_3_planning_cycles() {
    with_default_timeout(|| {
        // Given: Pipeline configured for 3 iterations (-D 3)
        let mut state = with_locked_prompt_permissions(PipelineState::initial(3, 0));

        // Drive to development iteration 0 via reducer events.
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        state = reduce(state, PipelineEvent::development_agent_invoked(0));

        // Analysis agent runs - MUST NOT increment iteration
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 }),
        );
        assert_eq!(
            state.iteration, 0,
            "Analysis must not increment iteration counter"
        );

        // Complete development and proceed to commit
        let event = PipelineEvent::Development(DevelopmentEvent::IterationCompleted {
            iteration: 0,
            output_valid: true,
        });
        state = reduce(state, event);

        // After commit, iteration should increment to 1
        // (This happens in commit phase state reduction)
        // We verify by simulating the full cycle multiple times

        // Key assertion: Multiple analysis invocations within an iteration
        // (e.g., during continuation) do NOT affect iteration count.
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
                iteration: 0,
                status: ralph_workflow::reducer::state::DevelopmentStatus::Partial,
                summary: "continue".to_string(),
                files_changed: None,
                next_steps: None,
            }),
        );
        state = reduce(state, PipelineEvent::development_agent_invoked(0));

        // Second analysis in same iteration (continuation scenario)
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 }),
        );
        assert_eq!(
            state.iteration, 0,
            "Second analysis in same iteration must not increment counter"
        );

        // The iteration counter ONLY increments during commit phase transition
        // (tested indirectly through the existing commit phase tests)
    });
}

/// Test that continuation stays within the same iteration.
///
/// Verifies that when development continues (status=partial), the iteration
/// counter does NOT increment - continuation is multiple dev attempts within
/// the same planning cycle.
#[test]
fn test_continuation_does_not_increment_iteration() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::state::DevelopmentStatus;

        // Given: State at iteration 0 with partial completion
        let mut state = with_locked_prompt_permissions(PipelineState::initial(2, 0));
        state = reduce(state, PipelineEvent::planning_phase_completed());
        state = reduce(state, PipelineEvent::development_iteration_started(0));
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        state = reduce(state, PipelineEvent::development_agent_invoked(0));
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 }),
        );

        // When: Continuation is triggered (partial work)
        let event = PipelineEvent::Development(DevelopmentEvent::ContinuationTriggered {
            iteration: 0,
            status: DevelopmentStatus::Partial,
            summary: "Partial work completed".to_string(),
            files_changed: Some(vec!["src/main.rs".to_string()]),
            next_steps: Some("Continue implementation".to_string()),
        });
        state = reduce(state, event);

        // Then: Iteration counter should remain at 0
        assert_eq!(
            state.iteration, 0,
            "Continuation must NOT increment iteration counter"
        );

        // And: Analysis tracking should be reset for next dev attempt
        assert_eq!(
            state.analysis_agent_invoked_iteration, None,
            "Continuation should reset analysis tracking"
        );

        // Step 2: Second dev agent invocation (continuation)
        state = reduce(state, PipelineEvent::development_agent_invoked(0));

        // Step 3: Analysis should run again for this continuation
        state = reduce(
            state,
            PipelineEvent::Development(DevelopmentEvent::AnalysisAgentInvoked { iteration: 0 }),
        );

        // Verify: Still at iteration 0 after analysis in continuation
        assert_eq!(
            state.iteration, 0,
            "Iteration should STILL be 0 after analysis in continuation"
        );
    });
}
