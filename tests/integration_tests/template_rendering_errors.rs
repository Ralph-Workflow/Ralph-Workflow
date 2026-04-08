//! Integration test for template rendering error handling.
//!
//! Verifies that prompt template rendering failures do not terminate the pipeline.
//! The pipeline should use fallback prompts and continue advancing through effects.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use ralph_workflow::agents::session::SessionDrain;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::prompts::analysis::{generate_analysis_prompt, generate_fix_analysis_prompt};
use ralph_workflow::prompts::template_variables::SessionCapabilities;
use ralph_workflow::reducer::determine_next_effect;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{PipelineEvent, PipelinePhase, PlanningEvent};
use ralph_workflow::reducer::state::{AgentChainState, PipelineState};
use ralph_workflow::reducer::state_reduction::reduce;
use ralph_workflow::workspace::MemoryWorkspace;

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;

#[test]
fn test_planning_template_includes_guidance_and_checklist() {
    with_default_timeout(|| {
        let template = include_str!("../../ralph-workflow/src/prompts/templates/planning_xml.txt");

        assert!(
            template.contains("Required sections:") || template.contains("REQUIRED SECTIONS"),
            "Planning template must list required sections."
        );
        assert!(
            template.contains("ralph_submit_artifact"),
            "Planning template must reference the submission tool."
        );
        assert!(
            template.contains("PHASE"),
            "Planning template should document the multi-phase approach."
        );
    });
}

#[test]
fn test_analysis_prompts_include_status_contract() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test();
        let (caps, flags) = SessionCapabilities::from_drain(SessionDrain::Development);
        let session_caps = SessionCapabilities::new(&caps, &flags);
        let analysis_prompt =
            generate_analysis_prompt("plan", "diff", false, &workspace, session_caps);
        assert!(analysis_prompt.contains("completed"));
        assert!(analysis_prompt.contains("partial"));
        assert!(analysis_prompt.contains("failed"));

        let fix_prompt = generate_fix_analysis_prompt(
            "issues",
            "diff",
            "fix_result",
            false,
            &workspace,
            session_caps,
        );
        assert!(fix_prompt.contains("completed"));
        assert!(fix_prompt.contains("partial"));
        assert!(fix_prompt.contains("failed"));
    });
}

/// Test that pipeline advances even when prompt preparation might fail.
///
/// This verifies that the effect handler's non-fatal error handling allows
/// the pipeline to continue advancing to the next effect.
#[test]
fn test_pipeline_advances_after_prompt_preparation() {
    with_default_timeout(|| {
        let mut state = PipelineState {
            agent_chain: AgentChainState::initial()
                .with_agents(
                    vec!["test-agent".to_string()],
                    vec![vec![]],
                    AgentRole::Developer,
                )
                .with_drain(ralph_workflow::agents::AgentDrain::Planning),
            context_cleaned: true,
            gitignore_entries_ensured: true,
            planning_required_files_cleaned_iteration: Some(0),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(1, 0),
            ))
        };
        state.phase = PipelinePhase::Planning;
        state.iteration = 0;

        // Simulate prompt preparation completing (even if it internally had issues)
        let event = PipelineEvent::Planning(PlanningEvent::PromptPrepared { iteration: 0 });
        let new_state = reduce(state, event);

        // Pipeline should advance to next effect (invoke planning agent)
        let next_effect = determine_next_effect(&new_state);
        assert!(
            matches!(next_effect, Effect::InvokePlanningAgent { .. }),
            "Pipeline should advance to InvokePlanningAgent, got {next_effect:?}"
        );
    });
}

/// Test that pipeline does not enter `AwaitingDevFix` solely due to prompt preparation.
///
/// Verifies that template rendering errors (now non-fatal) don't trigger dev-fix flow.
#[test]
fn test_no_dev_fix_for_prompt_preparation() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Planning;

        // Prompt preparation succeeds (with or without internal fallback)
        let event = PipelineEvent::Planning(PlanningEvent::PromptPrepared { iteration: 0 });
        let new_state = reduce(state, event);

        // Should NOT be in AwaitingDevFix phase
        assert_ne!(
            new_state.phase,
            PipelinePhase::AwaitingDevFix,
            "Prompt preparation should not trigger dev-fix flow"
        );
    });
}

/// Test that development phase advances after prompt preparation.
#[test]
fn test_development_advances_after_prompt_preparation() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::event::DevelopmentEvent;

        let mut state = PipelineState {
            agent_chain: AgentChainState::initial().with_agents(
                vec!["test-agent".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            ),
            context_cleaned: true,
            development_context_prepared_iteration: Some(0),
            development_required_files_cleaned_iteration: Some(0),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(1, 0),
            ))
        };
        state.phase = PipelinePhase::Development;
        state.iteration = 0;

        // Prompt preparation completes
        let event = PipelineEvent::Development(DevelopmentEvent::PromptPrepared { iteration: 0 });
        let new_state = reduce(state, event);

        // Should advance to agent invocation
        let next_effect = determine_next_effect(&new_state);
        assert!(
            matches!(
                next_effect,
                Effect::InvokeDevelopmentAgent { .. } | Effect::InvokeAnalysisAgent { .. }
            ),
            "Pipeline should advance to agent invocation, got {next_effect:?}"
        );
    });
}

/// Test that review phase advances after prompt preparation.
#[test]
fn test_review_advances_after_prompt_preparation() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::event::ReviewEvent;

        let mut state = PipelineState {
            agent_chain: AgentChainState::initial().with_agents(
                vec!["test-agent".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            ),
            context_cleaned: true,
            review_context_prepared_pass: Some(0),
            review_required_files_cleaned_pass: Some(0),
            ..with_locked_prompt_permissions(with_locked_prompt_permissions(
                PipelineState::initial(0, 1),
            ))
        };
        state.phase = PipelinePhase::Review;
        state.reviewer_pass = 0;

        // Prompt preparation completes
        let event = PipelineEvent::Review(ReviewEvent::PromptPrepared { pass: 0 });
        let new_state = reduce(state, event);

        // Should advance to agent invocation
        let next_effect = determine_next_effect(&new_state);
        assert!(
            matches!(next_effect, Effect::InvokeReviewAgent { .. }),
            "Pipeline should advance to InvokeReviewAgent, got {next_effect:?}"
        );
    });
}
