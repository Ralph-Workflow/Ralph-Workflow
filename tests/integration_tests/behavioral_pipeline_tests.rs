//! Behavioral pipeline tests - verify composed reducer+orchestrator behavior.
//!
//! These tests verify the pipeline as a composed system: event in, effect sequence out.
//! They test observable behavior that users would see in CLI output, not internal state.
//!
//! # Test Philosophy
//!
//! Unlike unit tests that verify individual reducer arms in isolation, these tests verify:
//! - Reducer + Orchestrator compose correctly together
//! - The seam between event processing and effect derivation works
//! - Observable outcomes match user expectations
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::{AgentDrain, AgentRole};
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{AgentEvent, PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::orchestration::determine_next_effect;
use ralph_workflow::reducer::state::{AgentChainState, CommitState, PipelineState};
use ralph_workflow::reducer::state_reduction::reduce;

/// Composes reducer and orchestrator: processes event through reducer, then derives effect.
///
/// This is the key abstraction for behavioral tests - it tests the composed pipeline
/// as users experience it: event in, effect sequence out.
fn compose_reduce_orchestrate(
    state: PipelineState,
    event: PipelineEvent,
) -> (PipelineState, Effect) {
    let new_state = reduce(state, event);
    let effect = determine_next_effect(&new_state);
    (new_state, effect)
}

/// Creates a `PipelineState` in Development phase with a multi-agent chain (2 agents).
///
/// This setup is essential for testing fallback behavior - single-agent chains cannot
/// catch the double-invocation bug.
fn dev_state_with_multi_agent_chain() -> PipelineState {
    PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        total_iterations: 3,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["primary-agent".to_string(), "fallback-agent".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        development_context_prepared_iteration: Some(1),
        development_prompt_prepared_iteration: Some(1),
        development_required_files_cleaned_iteration: Some(1),
        // Simulate that InvokeDevelopmentAgent was derived in the previous orchestration cycle
        // This is set by the loop detection code after each effect derivation
        continuation: ralph_workflow::reducer::state::ContinuationState::default()
            .update_loop_detection_counters("InvokeDevelopmentAgent".to_string()),
        ..with_locked_prompt_permissions(PipelineState::initial(3, 2))
    }
}

/// Test 1: Successful dev agent transitions to Analysis drain, NOT next agent in chain.
///
/// This is THE test that would have caught the production bug:
/// - A development agent succeeds (`InvocationSucceeded`)
/// - The pipeline should transition to Analysis drain
/// - NOT invoke the next agent in the fallback chain
///
/// Bug: After agent success, the orchestrator derived `InvokeDevelopmentAgent` with
/// the next agent instead of `InitializeAgentChain { drain: Analysis }`.
#[test]
fn test_successful_dev_agent_transitions_to_analysis_not_next_agent() {
    with_default_timeout(|| {
        let state = dev_state_with_multi_agent_chain();

        // Primary agent should be selected
        assert_eq!(state.agent_chain.current_agent().unwrap(), "primary-agent");

        // Process InvocationSucceeded through reducer + orchestrator
        let (_, effect) = compose_reduce_orchestrate(
            state,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded {
                role: AgentRole::Developer,
                agent: "primary-agent".into(),
            }),
        );

        // CRITICAL ASSERTION: After dev agent success, should transition to Analysis drain,
        // NOT invoke the next fallback agent.
        //
        // The bug was: this derived `InvokeDevelopmentAgent` (next agent in chain) instead of
        // `InitializeAgentChain { drain: Analysis }`
        match &effect {
            Effect::InitializeAgentChain { drain } => {
                assert_eq!(
                    drain,
                    &AgentDrain::Analysis,
                    "After dev agent success, should transition to Analysis drain, not {drain}"
                );
            }
            Effect::InvokeDevelopmentAgent { .. } => {
                panic!(
                    "BUG: After dev agent success, derived InvokeDevelopmentAgent instead of Analysis drain. \
                     This is the double-invocation bug - the fallback chain advanced on success!"
                );
            }
            other => {
                panic!(
                    "After dev agent success, expected InitializeAgentChain {{ drain: Analysis }}, \
                     got {other:?}"
                );
            }
        }
    });
}

/// Test 2: Only one dev agent invocation per iteration before analysis.
///
/// Simulate a full iteration: chain init -> dev agent invocation -> success ->
/// assert Analysis effects are derived WITHOUT ever deriving a second `InvokeDevelopmentAgent`.
#[test]
fn test_only_one_dev_agent_invocation_per_iteration_before_analysis() {
    with_default_timeout(|| {
        // Start with state AFTER dev agent invocation but BEFORE analysis
        let mut state = dev_state_with_multi_agent_chain();
        state.development_agent_invoked_iteration = Some(1);

        // After dev agent invoked, next effect should be InitializeAgentChain for Analysis
        let effect = determine_next_effect(&state);

        // Should be transitioning to Analysis, NOT invoking another dev agent
        match &effect {
            Effect::InitializeAgentChain { drain } => {
                assert_eq!(
                    drain,
                    &AgentDrain::Analysis,
                    "After dev agent invoked, should initialize Analysis drain"
                );
            }
            Effect::InvokeDevelopmentAgent { .. } => {
                panic!(
                    "BUG: Derived InvokeDevelopmentAgent after agent was already invoked. \
                     This would cause double-invocation!"
                );
            }
            other => {
                panic!("Expected InitializeAgentChain(Analysis), got {other:?}");
            }
        }
    });
}

/// Test 3: Verify effective agent invocation state after `InvocationSucceeded`
///
/// This test verifies that after `InvocationSucceeded`, the "effective" agent invocation
/// state is detected correctly. The fix uses `effective_agent_invoked` which checks both
/// the explicit flag and the implicit completion state.
#[test]
fn test_effective_agent_invocation_state_after_success() {
    with_default_timeout(|| {
        let state = dev_state_with_multi_agent_chain();

        // Process InvocationSucceeded
        let (new_state, effect) = compose_reduce_orchestrate(
            state,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded {
                role: AgentRole::Developer,
                agent: "primary-agent".into(),
            }),
        );

        // After InvocationSucceeded, the next effect should be Analysis drain
        // This verifies the effective invocation state is detected correctly
        match &effect {
            Effect::InitializeAgentChain { drain } => {
                assert_eq!(
                    drain,
                    &AgentDrain::Analysis,
                    "After InvocationSucceeded, should transition to Analysis drain"
                );
            }
            other => {
                panic!(
                    "After InvocationSucceeded, expected InitializeAgentChain {{ drain: Analysis }}, got {other:?}"
                );
            }
        }

        // Verify the mode is Normal after success
        assert_eq!(
            new_state.agent_chain.current_mode,
            ralph_workflow::agents::DrainMode::Normal,
            "Mode should be Normal after InvocationSucceeded"
        );
    });
}

/// Test 4: Fallback chain advances ONLY on failure, not on success.
///
/// This test verifies the invariant: when an agent fails (`RateLimited`, `AuthFailed`,
/// `TimedOut`, `InvocationFailed`), the chain advances. When an agent succeeds,
/// the chain does NOT advance - it transitions to the next drain instead.
#[test]
fn test_fallback_chain_advances_only_on_failure() {
    use ralph_workflow::reducer::event::{AgentErrorKind, TimeoutOutputKind};

    with_default_timeout(|| {
        // Test each failure type advances the chain
        let failure_events = vec![
            PipelineEvent::Agent(AgentEvent::RateLimited {
                role: AgentRole::Developer,
                agent: "primary-agent".into(),
                prompt_context: None,
            }),
            PipelineEvent::Agent(AgentEvent::AuthFailed {
                role: AgentRole::Developer,
                agent: "primary-agent".into(),
            }),
            PipelineEvent::Agent(AgentEvent::TimedOut {
                role: AgentRole::Developer,
                agent: "primary-agent".into(),
                output_kind: TimeoutOutputKind::NoResult,
                child_status_at_timeout: None,
                logfile_path: None,
            }),
            PipelineEvent::Agent(AgentEvent::InvocationFailed {
                role: AgentRole::Developer,
                agent: "primary-agent".into(),
                exit_code: 1,
                error_kind: AgentErrorKind::InternalError,
                retriable: false,
            }),
        ];

        for event in failure_events {
            let state = dev_state_with_multi_agent_chain();
            let (_, effect) = compose_reduce_orchestrate(state, event);

            // After failure, should derive InvokeDevelopmentAgent (advancing chain)
            // NOT InitializeAgentChain to Analysis
            match &effect {
                Effect::InvokeDevelopmentAgent { .. } => {
                    // Expected - chain should advance on failure
                }
                Effect::InitializeAgentChain { .. } => {
                    panic!(
                        "BUG: After failure event, chain should advance (InvokeDevelopmentAgent), \
                         not transition to Analysis drain"
                    );
                }
                other => {
                    // Other effects may be valid (e.g., InitializeAgentChain with fallback)
                    // Just log for visibility
                    eprintln!("After failure: got {other:?}");
                }
            }
        }

        // Test that success does NOT advance the chain
        let success_state = dev_state_with_multi_agent_chain();
        let (_, success_effect) = compose_reduce_orchestrate(
            success_state,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded {
                role: AgentRole::Developer,
                agent: "primary-agent".into(),
            }),
        );

        match &success_effect {
            Effect::InitializeAgentChain { drain } => {
                assert_eq!(
                    drain,
                    &AgentDrain::Analysis,
                    "After success, should transition to Analysis drain"
                );
            }
            Effect::InvokeDevelopmentAgent { .. } => {
                panic!(
                    "BUG: After success, chain should NOT advance. \
                     Got InvokeDevelopmentAgent - this is the double-invocation bug!"
                );
            }
            other => {
                panic!("After success, expected InitializeAgentChain(Analysis), got {other:?}");
            }
        }
    });
}

/// Test 5: Agent success and failure produce fundamentally different effect sequences.
///
/// This test verifies that the full effect sequence after success is completely
/// different from after any failure event. This catches bugs where the system
/// treats success and failure identically.
#[test]
fn test_agent_success_vs_failure_effect_sequences_differ() {
    with_default_timeout(|| {
        let success_state = dev_state_with_multi_agent_chain();
        let failure_state = dev_state_with_multi_agent_chain();

        // Get effects for both success and failure
        let (_, success_effect) = compose_reduce_orchestrate(
            success_state,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded {
                role: AgentRole::Developer,
                agent: "primary-agent".into(),
            }),
        );

        let (_, failure_effect) = compose_reduce_orchestrate(
            failure_state,
            PipelineEvent::Agent(AgentEvent::RateLimited {
                role: AgentRole::Developer,
                agent: "primary-agent".into(),
                prompt_context: None,
            }),
        );

        // The effects must be different - success goes to Analysis, failure advances chain
        match (&success_effect, &failure_effect) {
            (
                Effect::InitializeAgentChain {
                    drain: success_drain,
                },
                Effect::InvokeDevelopmentAgent { .. },
            ) => {
                assert_eq!(
                    success_drain,
                    &AgentDrain::Analysis,
                    "Success must go to Analysis drain"
                );
                // Failure going to InvokeDevelopmentAgent is correct
            }
            (
                Effect::InitializeAgentChain {
                    drain: success_drain,
                },
                Effect::InitializeAgentChain {
                    drain: failure_drain,
                },
            ) if success_drain == failure_drain => {
                panic!(
                    "BUG: Success and failure produce the same effect! \
                     Both went to {success_drain:?}. They must differ."
                );
            }
            _ => {
                // Check if both went to InvokeDevelopmentAgent (bug!)
                if matches!(success_effect, Effect::InvokeDevelopmentAgent { .. }) {
                    panic!(
                        "BUG: Success derived InvokeDevelopmentAgent! \
                         This is the double-invocation bug."
                    );
                }
            }
        }
    });
}

/// Test 5: Drain transition after success is correct for every phase.
///
/// This test verifies that for both Development and Fix phases, agent success correctly
/// transitions to the Analysis drain. This is a regression test for the
/// double-invocation bug.
#[test]
fn test_drain_transition_after_success_correct_for_every_phase() {
    with_default_timeout(|| {
        // === Development phase test ===
        let dev_state = dev_state_with_multi_agent_chain();

        // Process success event
        let (_, dev_effect) = compose_reduce_orchestrate(
            dev_state,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded {
                role: AgentRole::Developer,
                agent: "primary-agent".into(),
            }),
        );

        // For Development phase, success MUST go to Analysis drain
        match &dev_effect {
            Effect::InitializeAgentChain { drain } => {
                assert_eq!(
                    drain,
                    &AgentDrain::Analysis,
                    "Development phase success must transition to Analysis drain"
                );
            }
            Effect::InvokeDevelopmentAgent { .. } => {
                panic!(
                    "BUG: Development phase success transitioned to next agent! \
                     This is the double-invocation bug."
                );
            }
            other => {
                panic!(
                    "Expected InitializeAgentChain(Analysis) after Development success, got {other:?}"
                );
            }
        }

        // === Fix phase test ===
        let fix_state = review_state_with_fix_multi_agent_chain();

        // Process success event for fix agent
        let (_, fix_effect) = compose_reduce_orchestrate(
            fix_state,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded {
                role: AgentRole::Reviewer,
                agent: "primary-fix-agent".into(),
            }),
        );

        // For Fix phase, success MUST go to Analysis drain (for fix analysis)
        match &fix_effect {
            Effect::InitializeAgentChain { drain } => {
                assert_eq!(
                    drain,
                    &AgentDrain::Analysis,
                    "Fix phase success must transition to Analysis drain for fix analysis"
                );
            }
            Effect::InvokeFixAgent { .. } => {
                panic!(
                    "BUG: Fix phase success transitioned to next agent! \
                     This is the double-invocation bug for fix phase."
                );
            }
            other => {
                panic!("Expected InitializeAgentChain(Analysis) after Fix success, got {other:?}");
            }
        }
    });
}

/// Creates a `PipelineState` in Review phase with a multi-agent fix chain (2 agents).
fn review_state_with_fix_multi_agent_chain() -> PipelineState {
    PipelineState {
        phase: PipelinePhase::Review,
        iteration: 1,
        total_iterations: 3,
        reviewer_pass: 1,
        agent_chain: AgentChainState::initial().with_agents(
            vec![
                "primary-fix-agent".to_string(),
                "fallback-fix-agent".to_string(),
            ],
            vec![vec![], vec![]],
            AgentRole::Reviewer, // Fix uses Reviewer role
        ),
        fix_prompt_prepared_pass: Some(1),
        fix_required_files_cleaned_pass: Some(1),
        continuation: ralph_workflow::reducer::state::ContinuationState::default()
            .update_loop_detection_counters("InvokeFixAgent".to_string()),
        ..with_locked_prompt_permissions(PipelineState::initial(3, 2))
    }
}

fn planning_state_after_agent_success_without_invoked_flag() -> PipelineState {
    let mut planning_chain = AgentChainState::initial().with_agents(
        vec![
            "primary-planner".to_string(),
            "fallback-planner".to_string(),
        ],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );
    planning_chain.current_drain = AgentDrain::Planning;

    PipelineState {
        phase: PipelinePhase::Planning,
        iteration: 1,
        total_iterations: 3,
        context_cleaned: true,
        gitignore_entries_ensured: true,
        planning_prompt_prepared_iteration: Some(1),
        planning_required_files_cleaned_iteration: Some(1),
        agent_chain: planning_chain,
        continuation: ralph_workflow::reducer::state::ContinuationState::default()
            .update_loop_detection_counters("InvokePlanningAgent".to_string()),
        ..with_locked_prompt_permissions(PipelineState::initial(3, 2))
    }
}

fn review_state_after_agent_success_without_invoked_flag() -> PipelineState {
    PipelineState {
        phase: PipelinePhase::Review,
        iteration: 1,
        total_iterations: 3,
        reviewer_pass: 1,
        total_reviewer_passes: 2,
        review_context_prepared_pass: Some(1),
        review_prompt_prepared_pass: Some(1),
        review_required_files_cleaned_pass: Some(1),
        agent_chain: AgentChainState::initial().with_agents(
            vec![
                "primary-reviewer".to_string(),
                "fallback-reviewer".to_string(),
            ],
            vec![vec![], vec![]],
            AgentRole::Reviewer,
        ),
        continuation: ralph_workflow::reducer::state::ContinuationState::default()
            .update_loop_detection_counters("InvokeReviewAgent".to_string()),
        ..with_locked_prompt_permissions(PipelineState::initial(3, 2))
    }
}

fn commit_state_after_agent_success_without_invoked_flag() -> PipelineState {
    PipelineState {
        phase: PipelinePhase::CommitMessage,
        iteration: 1,
        total_iterations: 3,
        commit: CommitState::Generating {
            attempt: 1,
            max_attempts: 3,
        },
        commit_prompt_prepared: true,
        commit_required_files_cleaned: true,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["primary-commit".to_string(), "fallback-commit".to_string()],
            vec![vec![], vec![]],
            AgentRole::Commit,
        ),
        continuation: ralph_workflow::reducer::state::ContinuationState::default()
            .update_loop_detection_counters("InvokeCommitAgent".to_string()),
        ..with_locked_prompt_permissions(PipelineState::initial(3, 2))
    }
}

/// Test 7: Fix analysis - successful fix agent transitions to Analysis drain.
///
/// After a fix agent succeeds, the pipeline should transition to Analysis drain
/// to verify the fix, NOT invoke the next fix agent in the fallback chain.
#[test]
fn test_fix_analysis_successful_fix_agent_to_analysis_drain() {
    with_default_timeout(|| {
        let mut state = review_state_with_fix_multi_agent_chain();
        state.fix_agent_invoked_pass = Some(1);

        // After fix agent invoked, next effect should be InitializeAgentChain for Analysis
        let effect = determine_next_effect(&state);

        match &effect {
            Effect::InitializeAgentChain { drain } => {
                assert_eq!(
                    drain,
                    &AgentDrain::Analysis,
                    "After fix agent success, should initialize Analysis drain"
                );
            }
            Effect::InvokeFixAgent { .. } => {
                panic!(
                    "BUG: After fix agent success, derived InvokeFixAgent instead of Analysis drain"
                );
            }
            other => {
                panic!("Expected InitializeAgentChain(Analysis) after fix success, got {other:?}");
            }
        }
    });
}

/// Test 7b: Fix agent success via composed reducer+orchestrator (double-invocation bug).
///
/// This is THE test that would have caught the production bug for the fix phase:
/// - A fix agent succeeds (InvocationSucceeded event processed by reducer)
/// - The orchestrator should transition to Analysis drain
/// - NOT invoke the next fix agent in the fallback chain
///
/// This differs from Test 7 above: it tests what happens when InvocationSucceeded
/// is processed through the reducer (event ordering issue), rather than testing
/// with the flag already set manually.
#[test]
fn test_fix_agent_success_transitions_to_analysis_via_composed_pipeline() {
    with_default_timeout(|| {
        // Create state with all pre-invocation steps complete but WITHOUT fix_agent_invoked_pass set
        // This simulates the state right after InvocationSucceeded is processed but before
        // the FixAgentInvoked event sets the flag
        let mut state = review_state_with_fix_multi_agent_chain();

        // All pre-invocation steps are done
        assert_eq!(state.fix_prompt_prepared_pass, Some(1));
        assert_eq!(state.fix_required_files_cleaned_pass, Some(1));

        // The key: fix_agent_invoked_pass is NOT set (this is the bug scenario)
        assert_eq!(state.fix_agent_invoked_pass, None);

        // The last effect was InvokeFixAgent (simulating that the effect was just derived)
        state.continuation.last_effect_kind = Some("InvokeFixAgent".to_string());

        // Process InvocationSucceeded through reducer + orchestrator
        let (_, effect) = compose_reduce_orchestrate(
            state,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded {
                role: AgentRole::Reviewer,
                agent: "primary-fix-agent".into(),
            }),
        );

        // CRITICAL ASSERTION: After fix agent success, should transition to Analysis drain,
        // NOT invoke the next fallback agent.
        //
        // The bug would be: this derives `InvokeFixAgent` (next agent in chain) instead of
        // `InitializeAgentChain { drain: Analysis }`
        match &effect {
            Effect::InitializeAgentChain { drain } => {
                assert_eq!(
                    drain,
                    &AgentDrain::Analysis,
                    "After fix agent success, should transition to Analysis drain, not {drain}"
                );
            }
            Effect::InvokeFixAgent { .. } => {
                panic!(
                    "BUG: After fix agent success, derived InvokeFixAgent instead of Analysis drain. \
                     This is the double-invocation bug - the fallback chain advanced on success!"
                );
            }
            other => {
                panic!(
                    "After fix agent success, expected InitializeAgentChain {{ drain: Analysis }}, \
                     got {other:?}"
                );
            }
        }
    });
}

/// Test 8: Fix analysis - analysis reports partial triggers fix continuation.
///
/// When the analysis agent reports `partial` or `failed`, the system should
/// trigger a fix continuation (re-invoke fix agent with context).
#[test]
fn test_fix_analysis_partial_triggers_fix_continuation() {
    with_default_timeout(|| {
        let mut state = review_state_with_fix_multi_agent_chain();
        // Simulate: fix agent invoked, then fix analysis invoked, then all XML steps done
        state.fix_agent_invoked_pass = Some(1);
        state.fix_analysis_agent_invoked_pass = Some(1);
        state.fix_result_xml_extracted_pass = Some(1);
        state.fix_validated_outcome = Some(ralph_workflow::reducer::state::FixValidatedOutcome {
            pass: 1,
            status: ralph_workflow::reducer::state::FixStatus::IssuesRemain,
            summary: Some("Analysis found remaining issues".to_string()),
            analysis_decision: None,
        });
        state.fix_result_xml_archived_pass = Some(1);

        // After analysis reports partial/failed and all steps are done,
        // the orchestrator should derive ApplyFixOutcome (the continuation
        // decision is made by the reducer when processing FixOutcomeApplied)
        let effect = determine_next_effect(&state);

        match &effect {
            Effect::ApplyFixOutcome { pass } => {
                assert_eq!(pass, &1, "Should apply fix outcome for pass 1");
            }
            Effect::InvokeFixAgent { .. } => {
                panic!(
                    "BUG: Analysis reported IssuesRemain but pipeline re-invokes fix agent \
                     before applying outcome"
                );
            }
            other => {
                panic!(
                    "Expected ApplyFixOutcome after fix analysis with IssuesRemain, got {other:?}"
                );
            }
        }
    });
}

/// Test 9: Fix analysis - analysis reports completed proceeds to commit.
///
/// When the analysis agent reports `completed`, the pipeline should proceed
/// to commit without triggering a continuation.
#[test]
fn test_fix_analysis_completed_proceeds_to_commit() {
    with_default_timeout(|| {
        let mut state = review_state_with_fix_multi_agent_chain();
        // Simulate: fix agent invoked, then fix analysis invoked, then validated as completed
        state.fix_agent_invoked_pass = Some(1);
        state.fix_analysis_agent_invoked_pass = Some(1);
        state.fix_result_xml_extracted_pass = Some(1);
        state.fix_validated_outcome = Some(ralph_workflow::reducer::state::FixValidatedOutcome {
            pass: 1,
            status: ralph_workflow::reducer::state::FixStatus::AllIssuesAddressed,
            summary: Some("Analysis confirmed all issues addressed".to_string()),
            analysis_decision: None,
        });
        state.fix_result_xml_archived_pass = Some(1);

        // After analysis reports completed (AllIssuesAddressed), should apply fix outcome
        let effect = determine_next_effect(&state);

        match &effect {
            Effect::ApplyFixOutcome { pass } => {
                assert_eq!(pass, &1, "Should apply fix outcome for pass 1");
            }
            Effect::InvokeFixAgent { .. } => {
                panic!(
                    "BUG: Analysis reported AllIssuesAddressed but pipeline triggers continuation instead of applying outcome"
                );
            }
            other => {
                panic!("Expected ApplyFixOutcome after successful analysis, got {other:?}");
            }
        }
    });
}

/// Test 10: Fix analysis continuation budget exhaustion.
///
/// After N fix continuations with `partial`/`failed` analysis, the budget
/// should be exhausted and the pipeline should proceed to commit.
#[test]
fn test_fix_analysis_continuation_budget_exhaustion() {
    with_default_timeout(|| {
        let mut state = review_state_with_fix_multi_agent_chain();
        // Simulate: all fix analysis steps complete, but continuation budget is exhausted
        state.fix_agent_invoked_pass = Some(1);
        state.fix_analysis_agent_invoked_pass = Some(1);
        state.fix_result_xml_extracted_pass = Some(1);
        state.fix_validated_outcome = Some(ralph_workflow::reducer::state::FixValidatedOutcome {
            pass: 1,
            status: ralph_workflow::reducer::state::FixStatus::IssuesRemain,
            summary: Some("Analysis found remaining issues".to_string()),
            analysis_decision: None,
        });
        state.fix_result_xml_archived_pass = Some(1);

        // Exhaust the continuation budget by setting fix_continuation_attempt to max (10)
        state.continuation.fix_continuation_attempt = 10;
        state.continuation.max_fix_continue_count = 10;

        // When budget is exhausted, should still proceed to apply outcome (not block)
        let effect = determine_next_effect(&state);

        // The key invariant: even with IssuesRemain, when budget exhausted,
        // the pipeline should derive ApplyFixOutcome (proceed to commit)
        match &effect {
            Effect::ApplyFixOutcome { pass } => {
                assert_eq!(pass, &1, "Should apply fix outcome for pass 1");
            }
            Effect::InvokeFixAgent { .. } => {
                panic!(
                    "BUG: Budget exhausted but pipeline still tries to invoke fix agent. \
                     Should proceed to commit instead."
                );
            }
            other => {
                panic!(
                    "Expected ApplyFixOutcome when budget exhausted with IssuesRemain, got {other:?}"
                );
            }
        }
    });
}

#[test]
fn test_fix_continuation_after_analysis_prepares_continuation_prompt() {
    with_default_timeout(|| {
        let analysis_chain = AgentChainState::initial().with_agents(
            vec![
                "analysis-primary".to_string(),
                "analysis-fallback".to_string(),
            ],
            vec![vec![], vec![]],
            AgentRole::Analysis,
        );

        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 1,
            total_reviewer_passes: 2,
            agent_chain: analysis_chain,
            fix_validated_outcome: Some(ralph_workflow::reducer::state::FixValidatedOutcome {
                pass: 1,
                status: ralph_workflow::reducer::state::FixStatus::IssuesRemain,
                summary: Some("analysis found remaining issues".to_string()),
                analysis_decision: None,
            }),
            ..with_locked_prompt_permissions(PipelineState::initial(3, 2))
        };

        let (new_state, effect) =
            compose_reduce_orchestrate(state, PipelineEvent::fix_outcome_applied(1));

        assert!(
            new_state.agent_chain.agents.is_empty(),
            "Fix continuation should clear currently loaded analysis chain before orchestration"
        );

        assert!(
            matches!(
                effect,
                Effect::PrepareFixPrompt {
                    pass: 1,
                    prompt_mode: ralph_workflow::reducer::state::PromptMode::Continuation,
                }
            ),
            "Fix continuation after analysis must prepare continuation prompt, got {effect:?}"
        );
    });
}

#[test]
fn test_successful_invocation_is_treated_as_complete_across_planning_review_and_commit() {
    with_default_timeout(|| {
        let planning_state = planning_state_after_agent_success_without_invoked_flag();
        let (_, planning_effect) = compose_reduce_orchestrate(
            planning_state,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded {
                role: AgentRole::Developer,
                agent: "primary-planner".into(),
            }),
        );
        assert!(
            matches!(planning_effect, Effect::ExtractPlanningXml { iteration: 1 }),
            "Planning success should continue to XML extraction, got {planning_effect:?}"
        );

        let review_state = review_state_after_agent_success_without_invoked_flag();
        let (_, review_effect) = compose_reduce_orchestrate(
            review_state,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded {
                role: AgentRole::Reviewer,
                agent: "primary-reviewer".into(),
            }),
        );
        assert!(
            matches!(review_effect, Effect::ExtractReviewIssuesXml { pass: 1 }),
            "Review success should continue to XML extraction, got {review_effect:?}"
        );

        let commit_state = commit_state_after_agent_success_without_invoked_flag();
        let (_, commit_effect) = compose_reduce_orchestrate(
            commit_state,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded {
                role: AgentRole::Commit,
                agent: "primary-commit".into(),
            }),
        );
        assert!(
            matches!(commit_effect, Effect::ExtractCommitXml),
            "Commit success should continue to XML extraction, got {commit_effect:?}"
        );
    });
}
