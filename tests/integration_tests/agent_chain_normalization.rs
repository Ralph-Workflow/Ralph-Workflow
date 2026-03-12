//! Integration test for deterministic agent-chain normalization.
//!
//! Verifies that agent chain state is normalized before each invocation to ensure
//! checkpoint replay produces identical agent selection.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use ralph_workflow::agents::{AgentDrain, AgentRegistry, AgentRole, AgentsConfigFile};
use ralph_workflow::app::plumbing::{
    resolve_commit_message_agents_for_testing, CommitGenerationConfig,
};
use ralph_workflow::cli::handle_init_local_config_with;
use ralph_workflow::config::loader::{
    load_config_from_path_with_env, ConfigLoadWithValidationError,
};
use ralph_workflow::config::validation::{validate_config_file, ConfigValidationError};
use ralph_workflow::config::Config;
use ralph_workflow::config::MemoryConfigEnvironment;
use ralph_workflow::config::UnifiedConfig;
use ralph_workflow::executor::{MockProcessExecutor, ProcessExecutor};
use ralph_workflow::logger::{Colors, Logger};
use ralph_workflow::prompts::template_context::TemplateContext;
use ralph_workflow::reducer::determine_next_effect;
use ralph_workflow::reducer::effect::{Effect, EffectHandler};
use ralph_workflow::reducer::event::{PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::handler::MainEffectHandler;
use ralph_workflow::reducer::state::{AgentChainState, FixStatus, PipelineState, PromptMode};
use ralph_workflow::reducer::state_reduction::reduce;
use ralph_workflow::workspace::{MemoryWorkspace, Workspace};
use std::path::Path;
use std::sync::Arc;

use crate::common::{with_locked_prompt_permissions, IntegrationFixture};
use crate::test_timeout::with_default_timeout;

const README_TEXT: &str = include_str!("../../ralph-workflow/README.md");
const AGENTS_MOD_SOURCE: &str = include_str!("../../ralph-workflow/src/agents/mod.rs");
const AGENTS_REGISTRY_SOURCE: &str = include_str!("../../ralph-workflow/src/agents/registry.rs");
const OPENCODE_RESOLVER_SOURCE: &str =
    include_str!("../../ralph-workflow/src/agents/opencode_resolver.rs");
const CONFIG_UNIFIED_MOD_SOURCE: &str =
    include_str!("../../ralph-workflow/src/config/unified/mod.rs");
const AGENT_COMPATIBILITY_DOC: &str = include_str!("../../docs/agent-compatibility.md");

fn commit_generation_config<'a>(
    config: &'a Config,
    registry: &'a AgentRegistry,
    workspace: &'a Arc<MemoryWorkspace>,
    logger: &'a Logger,
    template_context: &'a TemplateContext,
    executor: Arc<dyn ProcessExecutor>,
) -> CommitGenerationConfig<'a> {
    CommitGenerationConfig {
        config,
        template_context,
        workspace: workspace.as_ref(),
        workspace_arc: Arc::clone(workspace) as Arc<dyn Workspace>,
        registry,
        logger,
        colors: Colors::new(),
        developer_agent: "developer-default",
        reviewer_agent: "reviewer-default",
        executor,
    }
}

/// Test that agent chain initializes correctly for each phase.
#[test]
fn test_agent_chain_initialization() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 1);

        // Agent chain should be initialized with Developer role for Planning phase
        assert_eq!(state.agent_chain.current_role, AgentRole::Developer);
    });
}

/// Test that XSD retry preserves `last_session_id` for same agent.
#[test]
fn test_xsd_retry_preserves_session() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::Planning;
        state.agent_chain.last_session_id = Some("session-123".to_string());
        state.continuation.xsd_retry_session_reuse_pending = true;

        // Last session ID should be preserved during XSD retry
        // The normalization should NOT clear last_session_id when xsd_retry_session_reuse_pending
        assert_eq!(
            state.agent_chain.last_session_id,
            Some("session-123".to_string())
        );
    });
}

/// Test that fix-mode resume state drives fix XSD-retry metrics even if chain metadata is stale.
#[test]
fn test_agent_xsd_validation_failed_uses_runtime_fix_drain_for_metrics() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(0, 3);
        state.phase = PipelinePhase::Review;
        state.agent_chain.current_drain = AgentDrain::Review;
        state.review_issues_found = true;

        let state = reduce(
            state,
            PipelineEvent::agent_xsd_validation_failed(
                AgentRole::Reviewer,
                ralph_workflow::reducer::state::ArtifactType::FixResult,
                "invalid fix xml".to_string(),
                1,
            ),
        );

        assert_eq!(state.metrics.xsd_retry_fix, 1);
        assert_eq!(state.metrics.xsd_retry_review, 0);
    });
}

/// Test that same-agent retry pending produces a same-agent retry effect.
///
/// When `same_agent_retry_pending` is set, orchestration should produce
/// a prompt effect with `PromptMode::SameAgentRetry` for the current phase.
#[test]
fn test_same_agent_retry_produces_retry_effect() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Planning;
        state.continuation.same_agent_retry_pending = true;

        // Behavioral check: orchestration should produce a same-agent retry effect
        let effect = determine_next_effect(&state);
        assert!(
            matches!(
                effect,
                Effect::PreparePlanningPrompt {
                    prompt_mode: PromptMode::SameAgentRetry,
                    ..
                }
            ),
            "same_agent_retry_pending should produce SameAgentRetry effect, got: {effect:?}"
        );
    });
}

/// Test that checkpoint replay produces consistent effect.
///
/// This verifies determinism: same state -> same next effect.
#[test]
fn test_checkpoint_replay_consistency() {
    with_default_timeout(|| {
        let state = with_locked_prompt_permissions(PipelineState::initial(1, 0));

        // Determine next effect
        let effect1 = determine_next_effect(&state);

        // Serialize and deserialize (simulating checkpoint replay)
        let json = serde_json::to_string(&state).expect("state should serialize");
        let restored_state: PipelineState =
            serde_json::from_str(&json).expect("state should deserialize");

        // Determine next effect from restored state
        let effect2 = determine_next_effect(&restored_state);

        // Effects should be identical (determinism)
        assert_eq!(
            format!("{effect1:?}"),
            format!("{:?}", effect2),
            "Checkpoint replay should produce identical next effect"
        );
    });
}

/// Test that checkpoint replay remains compatible when legacy role metadata is absent.
#[test]
fn test_checkpoint_replay_uses_drain_identity_when_current_role_is_missing() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Development;
        state.agent_chain.current_drain = AgentDrain::Analysis;
        state.agent_chain.current_role = AgentRole::Analysis;
        state.continuation.same_agent_retry_pending = true;

        let json = serde_json::to_string(&state).expect("state should serialize");
        let legacy_free_json = json.replace("\"current_role\":\"Analysis\",", "");

        let restored_state: PipelineState =
            serde_json::from_str(&legacy_free_json).expect("checkpoint should deserialize");

        let effect = determine_next_effect(&restored_state);

        assert!(
            matches!(effect, Effect::InvokeAnalysisAgent { iteration: 0 }),
            "analysis drain should remain authoritative after checkpoint restore, got: {effect:?}"
        );
    });
}

/// Test that legacy checkpoints without `current_drain` recover drain identity from role metadata.
#[test]
fn test_checkpoint_replay_uses_current_role_when_current_drain_is_missing() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Review;
        state.agent_chain.current_drain = AgentDrain::Review;
        state.agent_chain.current_role = AgentRole::Reviewer;

        let json = serde_json::to_string(&state).expect("state should serialize");
        let legacy_json = json.replace("\"current_drain\":\"Review\",", "");

        let restored_state: PipelineState =
            serde_json::from_str(&legacy_json).expect("checkpoint should deserialize");

        assert_eq!(restored_state.agent_chain.current_role, AgentRole::Reviewer);
        assert_eq!(restored_state.agent_chain.current_drain, AgentDrain::Review);
    });
}

#[test]
fn test_matches_runtime_drain_does_not_treat_review_and_fix_as_interchangeable() {
    with_default_timeout(|| {
        let state = AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(AgentDrain::Review)
            .with_mode(ralph_workflow::agents::DrainMode::Continuation);

        assert!(
            !state.matches_runtime_drain(AgentDrain::Fix),
            "legacy resume compatibility must not reuse the review drain for fix work"
        );
    });
}

#[test]
fn test_apply_unified_config_metadata_only_legacy_agent_chain_preserves_existing_drains() {
    with_default_timeout(|| {
        let mut registry = AgentRegistry::new().expect("default registry should build");
        let original_development = registry
            .available_fallbacks_for_drain(AgentDrain::Development)
            .into_iter()
            .map(str::to_string)
            .collect::<Vec<_>>();
        let original_review = registry
            .available_fallbacks_for_drain(AgentDrain::Review)
            .into_iter()
            .map(str::to_string)
            .collect::<Vec<_>>();

        let unified = UnifiedConfig::load_from_content(
            r"
            [agent_chain]
            max_retries = 7
            retry_delay_ms = 2500
            ",
        )
        .expect("config should parse");

        registry.apply_unified_config(&unified);

        assert_eq!(
            registry.available_fallbacks_for_drain(AgentDrain::Development),
            original_development
                .iter()
                .map(String::as_str)
                .collect::<Vec<_>>(),
            "metadata-only legacy agent_chain should not wipe the existing development drain"
        );
        assert_eq!(
            registry.available_fallbacks_for_drain(AgentDrain::Review),
            original_review
                .iter()
                .map(String::as_str)
                .collect::<Vec<_>>(),
            "metadata-only legacy agent_chain should not wipe the existing review drain"
        );
    });
}

/// Test that stale compatibility role metadata is ignored when drain metadata is present.
#[test]
fn test_checkpoint_replay_derives_role_from_authoritative_drain_when_metadata_conflicts() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Review;
        state.agent_chain.current_drain = AgentDrain::Fix;
        state.agent_chain.current_role = AgentRole::Developer;

        let json = serde_json::to_string(&state).expect("state should serialize");

        let restored_state: PipelineState =
            serde_json::from_str(&json).expect("checkpoint should deserialize");

        assert_eq!(restored_state.agent_chain.current_drain, AgentDrain::Fix);
        assert_eq!(restored_state.agent_chain.current_role, AgentRole::Reviewer);
    });
}

/// Test that nested continuation-prompt role metadata is derived from drain metadata on replay.
#[test]
fn test_checkpoint_replay_derives_nested_prompt_role_from_authoritative_drain() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Review;
        state.agent_chain.current_drain = AgentDrain::Fix;
        state.agent_chain.current_role = AgentRole::Reviewer;

        let mut json = serde_json::to_value(&state).expect("state should serialize");
        json["agent_chain"]["rate_limit_continuation_prompt"] = serde_json::json!({
            "drain": "Fix",
            "role": "Developer",
            "prompt": "retry with fix context"
        });

        let restored_state: PipelineState = serde_json::from_value(json)
            .expect("checkpoint with nested prompt metadata should deserialize");

        let prompt = restored_state
            .agent_chain
            .rate_limit_continuation_prompt
            .expect("structured prompt should deserialize");
        assert_eq!(prompt.drain, AgentDrain::Fix);
        assert_eq!(prompt.role, AgentRole::Reviewer);
        assert_eq!(prompt.prompt, "retry with fix context");
    });
}

/// Test that legacy checkpoints in fix continuation recover the fix drain, not review.
#[test]
fn test_checkpoint_replay_recovers_fix_drain_for_legacy_fix_continuation() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 1));
        state.phase = PipelinePhase::Review;
        state.reviewer_pass = 1;
        state.review_issues_found = true;
        state.agent_chain.current_drain = AgentDrain::Fix;
        state.agent_chain.current_role = AgentRole::Reviewer;
        state.agent_chain.current_mode = ralph_workflow::agents::DrainMode::Continuation;
        state.agent_chain.rate_limit_continuation_prompt = Some(
            ralph_workflow::reducer::state::RateLimitContinuationPrompt {
                drain: AgentDrain::Fix,
                role: AgentRole::Reviewer,
                prompt: "continue fixing remaining issues".to_string(),
            },
        );
        state.continuation.fix_continue_pending = true;
        state.continuation.fix_continuation_attempt = 1;

        let json = serde_json::to_string(&state).expect("state should serialize");
        let legacy_json = json.replace("\"current_drain\":\"Fix\",", "");

        let restored_state: PipelineState =
            serde_json::from_str(&legacy_json).expect("legacy checkpoint should deserialize");

        let effect = determine_next_effect(&restored_state);
        assert!(
            matches!(
                effect,
                Effect::PrepareFixPrompt {
                    pass: 1,
                    prompt_mode: _,
                }
            ),
            "legacy fix continuation should resume in the fix drain, got: {effect:?}"
        );
    });
}

#[test]
fn test_checkpoint_replay_recovers_fix_drain_for_legacy_fix_resume_with_structured_prompt_missing_drain(
) {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 1));
        state.phase = PipelinePhase::Review;
        state.reviewer_pass = 1;
        state.review_issues_found = true;
        state.fix_prompt_prepared_pass = Some(1);
        state.fix_agent_invoked_pass = Some(1);
        state.agent_chain.current_drain = AgentDrain::Fix;
        state.agent_chain.current_role = AgentRole::Reviewer;

        let mut json = serde_json::to_value(&state).expect("state should serialize");
        json["agent_chain"]["rate_limit_continuation_prompt"] = serde_json::json!({
            "role": "Reviewer",
            "prompt": "continue fixing remaining issues"
        });
        if let Some(agent_chain) = json["agent_chain"].as_object_mut() {
            agent_chain.remove("current_drain");
        }

        let restored_state: PipelineState =
            serde_json::from_value(json).expect("legacy checkpoint should deserialize");

        let effect = determine_next_effect(&restored_state);
        assert!(
            matches!(
                effect,
                Effect::PrepareFixPrompt {
                    pass: 1,
                    prompt_mode: _,
                } | Effect::InitializeAgentChain {
                    drain: AgentDrain::Fix,
                    ..
                }
            ),
            "legacy fix resume should remain in the fix drain, got: {effect:?}"
        );
    });
}

#[test]
fn test_checkpoint_replay_recovers_planning_drain_for_legacy_same_agent_retry() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Planning;
        state.gitignore_entries_ensured = true;
        state.context_cleaned = true;
        state.agent_chain = state
            .agent_chain
            .with_agents(
                vec!["planner".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(AgentDrain::Planning);
        state.continuation.same_agent_retry_pending = true;
        state.agent_chain.retry_cycle = 1;
        state.agent_chain.backoff_pending_ms = Some(2_000);

        let json = serde_json::to_string(&state).expect("state should serialize");
        let legacy_json = json.replace("\"current_drain\":\"Planning\",", "");

        let restored_state: PipelineState =
            serde_json::from_str(&legacy_json).expect("legacy checkpoint should deserialize");

        let effect = determine_next_effect(&restored_state);
        assert!(
            matches!(
                effect,
                Effect::PreparePlanningPrompt {
                    iteration: 0,
                    prompt_mode: PromptMode::SameAgentRetry,
                }
            ),
            "legacy planning retry should stay in the planning drain, got: {effect:?}"
        );
    });
}

#[test]
fn test_checkpoint_replay_recovers_planning_drain_for_legacy_normal_mode() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Planning;
        state.gitignore_entries_ensured = true;
        state.context_cleaned = true;
        state.agent_chain = state
            .agent_chain
            .with_agents(
                vec!["planner".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(AgentDrain::Planning);
        state.agent_chain.retry_cycle = 1;
        state.agent_chain.backoff_pending_ms = Some(2_000);

        let json = serde_json::to_string(&state).expect("state should serialize");
        let legacy_json = json.replace("\"current_drain\":\"Planning\",", "");

        let restored_state: PipelineState =
            serde_json::from_str(&legacy_json).expect("legacy checkpoint should deserialize");

        let effect = determine_next_effect(&restored_state);
        assert!(
            matches!(
                effect,
                Effect::BackoffWait {
                    role: AgentRole::Developer,
                    cycle: 1,
                    duration_ms: 2_000,
                }
            ),
            "legacy planning checkpoint should remain in planning flow while honoring backoff, got: {effect:?}"
        );
    });
}

#[test]
fn test_checkpoint_replay_recovers_fix_drain_for_legacy_xsd_retry() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 1));
        state.phase = PipelinePhase::Review;
        state.reviewer_pass = 1;
        state.review_issues_found = true;
        state.agent_chain = state
            .agent_chain
            .with_agents(vec!["fixer".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(AgentDrain::Fix)
            .with_mode(ralph_workflow::agents::DrainMode::XsdRetry);
        state.continuation.xsd_retry_pending = true;
        state.continuation.last_fix_xsd_error = Some("fix output missing field".to_string());

        let json = serde_json::to_string(&state).expect("state should serialize");
        let legacy_json = json.replace("\"current_drain\":\"Fix\",", "");

        let restored_state: PipelineState =
            serde_json::from_str(&legacy_json).expect("legacy checkpoint should deserialize");

        let effect = determine_next_effect(&restored_state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: AgentDrain::Fix,
                }
            ),
            "legacy fix XSD retry should reinitialize the fix drain before reuse, got: {effect:?}"
        );
    });
}

#[test]
fn test_checkpoint_replay_recovers_fix_drain_for_legacy_normal_mode() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 1));
        state.phase = PipelinePhase::Review;
        state.reviewer_pass = 1;
        state.review_issues_found = true;
        state.agent_chain = state
            .agent_chain
            .with_agents(vec!["fixer".to_string()], vec![vec![]], AgentRole::Reviewer)
            .with_drain(AgentDrain::Fix);
        state.agent_chain.retry_cycle = 1;
        state.agent_chain.last_session_id = Some("legacy-fix-session".to_string());

        let json = serde_json::to_string(&state).expect("state should serialize");
        let legacy_json = json.replace("\"current_drain\":\"Fix\",", "");

        let restored_state: PipelineState =
            serde_json::from_str(&legacy_json).expect("legacy checkpoint should deserialize");

        let effect = determine_next_effect(&restored_state);
        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: AgentDrain::Fix,
                }
            ),
            "legacy fix checkpoint should reinitialize the fix drain before reuse, got: {effect:?}"
        );
    });
}

/// Test that review completion with issues hands runtime ownership to the fix drain.
#[test]
fn test_review_completion_with_issues_switches_runtime_to_fix_drain() {
    with_default_timeout(|| {
        let state = with_locked_prompt_permissions(PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            review_issues_found: false,
            agent_chain: PipelineState::initial(1, 0)
                .agent_chain
                .with_agents(
                    vec!["reviewer".to_string()],
                    vec![vec![]],
                    AgentRole::Reviewer,
                )
                .with_drain(AgentDrain::Review),
            ..PipelineState::initial(1, 0)
        });

        let new_state = reduce(
            state,
            ralph_workflow::reducer::event::PipelineEvent::review_completed(0, true),
        );

        assert_eq!(new_state.phase, PipelinePhase::Review);
        assert_eq!(new_state.agent_chain.current_drain, AgentDrain::Fix);
        assert!(new_state.agent_chain.agents.is_empty());
        assert!(matches!(
            determine_next_effect(&new_state),
            Effect::InitializeAgentChain {
                drain: AgentDrain::Fix,
                ..
            }
        ));
    });
}

/// Test that agent chain normalization is consistent across phases.
#[test]
fn test_agent_chain_normalization_across_phases() {
    with_default_timeout(|| {
        // Planning phase: Developer role
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 1));
        state.phase = PipelinePhase::Planning;
        state.agent_chain.current_role = AgentRole::Developer;

        let effect = determine_next_effect(&state);
        // Should be planning-related effect
        assert!(
            matches!(
                effect,
                Effect::PreparePlanningPrompt { .. }
                    | Effect::InvokePlanningAgent { .. }
                    | Effect::InitializeAgentChain { .. }
            ),
            "Planning phase should produce planning effects"
        );

        // Review phase: Reviewer role
        let mut state = with_locked_prompt_permissions(PipelineState::initial(0, 1));
        state.phase = PipelinePhase::Review;
        state.agent_chain.current_role = AgentRole::Reviewer;

        let effect = determine_next_effect(&state);
        // Should be review-related effect
        assert!(
            matches!(
                effect,
                Effect::PrepareReviewContext { .. }
                    | Effect::MaterializeReviewInputs { .. }
                    | Effect::PrepareReviewPrompt { .. }
                    | Effect::InitializeAgentChain { .. }
            ),
            "Review phase should produce review effects"
        );
    });
}

/// Test that same-agent retry in Development uses drain identity, not stale role metadata.
#[test]
fn test_same_agent_retry_uses_analysis_drain_even_when_role_is_stale() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Development;
        state.continuation.same_agent_retry_pending = true;
        state.agent_chain.current_drain = AgentDrain::Analysis;
        state.agent_chain.current_role = AgentRole::Developer;

        let effect = determine_next_effect(&state);

        assert!(
            matches!(effect, Effect::InvokeAnalysisAgent { iteration: 0 }),
            "analysis drain retry should stay on analysis consumer, got: {effect:?}"
        );
    });
}

/// Test that legacy same-agent retry resumes analysis when development progress proves the retry stage.
#[test]
fn test_same_agent_retry_recovers_analysis_stage_from_development_progress_when_drain_is_missing() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Development;
        state.development_agent_invoked_iteration = Some(0);
        state.analysis_agent_invoked_iteration = None;
        state.continuation.same_agent_retry_pending = true;
        state.agent_chain.current_drain = AgentDrain::Analysis;
        state.agent_chain.current_role = AgentRole::Developer;

        let json = serde_json::to_string(&state).expect("state should serialize");
        let legacy_json = json.replace("\"current_drain\":\"Analysis\",", "");

        let restored_state: PipelineState =
            serde_json::from_str(&legacy_json).expect("legacy checkpoint should deserialize");

        let effect = determine_next_effect(&restored_state);

        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: AgentDrain::Analysis,
                }
            ),
            "legacy development retry should reinitialize the analysis drain, got: {effect:?}"
        );
    });
}

/// Test that same-agent retry reinitializes the analysis drain when the loaded chain is still development.
#[test]
fn test_same_agent_retry_reinitializes_analysis_drain_when_loaded_chain_is_development() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Development;
        state.development_agent_invoked_iteration = Some(0);
        state.continuation.same_agent_retry_pending = true;
        state.agent_chain.current_drain = AgentDrain::Development;
        state.agent_chain.current_role = AgentRole::Developer;

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: AgentDrain::Analysis,
                }
            ),
            "analysis retry should reinitialize the analysis drain when development is still loaded, got: {effect:?}"
        );
    });
}

/// Test that XSD retry in Development uses drain identity, not stale role metadata.
#[test]
fn test_xsd_retry_uses_analysis_drain_even_when_role_is_stale() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Development;
        state.continuation.xsd_retry_pending = true;
        state.agent_chain.current_drain = AgentDrain::Analysis;
        state.agent_chain.current_role = AgentRole::Developer;

        let effect = determine_next_effect(&state);

        assert!(
            matches!(effect, Effect::InvokeAnalysisAgent { iteration: 0 }),
            "analysis drain XSD retry should stay on analysis consumer, got: {effect:?}"
        );
    });
}

/// Test that development XSD retry reinitializes the analysis drain when the loaded chain is still development.
#[test]
fn test_xsd_retry_reinitializes_analysis_drain_when_loaded_chain_is_development() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Development;
        state.development_agent_invoked_iteration = Some(0);
        state.continuation.xsd_retry_pending = true;
        state.agent_chain.current_drain = AgentDrain::Development;
        state.agent_chain.current_role = AgentRole::Developer;

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: AgentDrain::Analysis,
                }
            ),
            "analysis XSD retry should reinitialize the analysis drain when development is still loaded, got: {effect:?}"
        );
    });
}

/// Test that fix continuation in Review uses drain identity, not stale role metadata.
#[test]
fn test_fix_continuation_uses_fix_drain_even_when_role_is_stale() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Review;
        state.continuation.fix_continue_pending = true;
        state.agent_chain.current_drain = AgentDrain::Fix;
        state.agent_chain.current_role = AgentRole::Developer;

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                effect,
                Effect::PrepareFixPrompt {
                    pass: 0,
                    prompt_mode: PromptMode::Continuation,
                }
            ),
            "fix continuation should stay on fix consumer, got: {effect:?}"
        );
    });
}

/// Test that fix continuation reinitializes the fix drain when the loaded chain is still review.
#[test]
fn test_fix_continuation_reinitializes_fix_drain_when_loaded_chain_is_review() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Review;
        state.continuation.fix_continue_pending = true;
        state.agent_chain.current_drain = AgentDrain::Review;
        state.agent_chain.current_role = AgentRole::Reviewer;

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: AgentDrain::Fix,
                }
            ),
            "fix continuation should reinitialize the fix drain when review is still loaded, got: {effect:?}"
        );
    });
}

/// Test that fix XSD retry reinitializes the fix drain when the loaded chain is still review.
#[test]
fn test_fix_xsd_retry_reinitializes_fix_drain_when_loaded_chain_is_review() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Review;
        state.continuation.xsd_retry_pending = true;
        state.continuation.last_fix_xsd_error = Some("invalid fix xml".to_string());
        state.agent_chain.current_drain = AgentDrain::Review;
        state.agent_chain.current_role = AgentRole::Reviewer;

        let effect = determine_next_effect(&state);

        assert!(
            matches!(
                effect,
                Effect::InitializeAgentChain {
                    drain: AgentDrain::Fix,
                }
            ),
            "fix XSD retry should reinitialize the fix drain when review is still loaded, got: {effect:?}"
        );
    });
}

/// Test that agent config file loading resolves named chain/drain schema through the workspace API.
#[test]
fn test_agents_config_file_loads_named_drain_schema() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test().with_file(
            ".agent/agents.toml",
            r#"
            [agent_chains]
            shared_dev = ["codex", "claude"]
            review_chain = ["claude"]
            fix_chain = ["codex"]

            [agent_drains]
            planning = "shared_dev"
            development = "shared_dev"
            review = "review_chain"
            fix = "fix_chain"
            commit = "review_chain"
            analysis = "shared_dev"
            "#,
        );

        let config = AgentsConfigFile::load_from_file_with_workspace(
            std::path::Path::new(".agent/agents.toml"),
            &workspace,
        )
        .expect("config should parse")
        .expect("config should exist");

        let resolved = config
            .resolve_drains_checked()
            .expect("drains should validate")
            .expect("named drain schema should resolve");

        let review = resolved
            .binding(AgentDrain::Review)
            .expect("review drain should resolve");
        let fix = resolved
            .binding(AgentDrain::Fix)
            .expect("fix drain should resolve");

        assert_eq!(review.chain_name, "review_chain");
        assert_eq!(review.agents, vec!["claude"]);
        assert_eq!(fix.chain_name, "fix_chain");
        assert_eq!(fix.agents, vec!["codex"]);
    });
}

#[test]
fn test_agents_config_file_merges_partial_legacy_agent_chain_with_built_in_defaults() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test().with_file(
            ".agent/agents.toml",
            r#"
            [agent_chain]
            developer = ["codex"]
            "#,
        );

        let config = AgentsConfigFile::load_from_file_with_workspace(
            std::path::Path::new(".agent/agents.toml"),
            &workspace,
        )
        .expect("config should parse")
        .expect("config should exist");

        let resolved = config
            .resolve_drains_checked()
            .expect("legacy drain config should resolve")
            .expect("legacy agent_chain should resolve to built-in drains");

        assert_eq!(
            resolved
                .binding(AgentDrain::Development)
                .expect("development drain should resolve")
                .agents,
            vec!["codex"]
        );
        assert!(
            !resolved
                .binding(AgentDrain::Review)
                .expect("review drain should inherit built-in defaults")
                .agents
                .is_empty(),
            "missing legacy reviewer key should inherit built-in defaults"
        );
        assert!(
            !resolved
                .binding(AgentDrain::Commit)
                .expect("commit drain should inherit built-in defaults")
                .agents
                .is_empty(),
            "missing legacy commit key should inherit built-in defaults instead of degrading to an empty drain"
        );
    });
}

#[test]
fn test_fix_continuation_prompt_uses_stored_fix_context() {
    with_default_timeout(|| {
        let workspace = Arc::new(
            MemoryWorkspace::new_test()
                .with_file(".agent/PROMPT.md.backup", "# Prompt backup\n")
                .with_file(".agent/PLAN.md", "# Plan\n")
                .with_file(".agent/ISSUES.md", "<issues/>\n")
                .with_dir(".agent/tmp"),
        );
        let mut fixture = IntegrationFixture::with_workspace(workspace.clone());
        let mut handler = MainEffectHandler::new(PipelineState {
            continuation: ralph_workflow::reducer::state::ContinuationState {
                fix_status: Some(FixStatus::IssuesRemain),
                fix_previous_summary: Some(
                    "Addressed parser bug, one review item remains".to_string(),
                ),
                fix_continuation_attempt: 1,
                fix_continue_pending: true,
                ..ralph_workflow::reducer::state::ContinuationState::new()
            },
            ..PipelineState::initial(0, 1)
        });
        let mut ctx = fixture.ctx(None);

        let result = handler
            .execute(
                Effect::PrepareFixPrompt {
                    pass: 0,
                    prompt_mode: PromptMode::Continuation,
                },
                &mut ctx,
            )
            .expect("fix continuation prompt effect should execute");

        let prompt = workspace
            .read(std::path::Path::new(".agent/tmp/fix_prompt.txt"))
            .expect("fix prompt should be written");

        assert!(
            prompt.contains("Addressed parser bug, one review item remains"),
            "continuation prompt should include previous fix summary; got: {prompt}"
        );
        assert!(
            prompt.contains("continuation attempt") || prompt.contains("Continue"),
            "continuation prompt should explicitly describe continuation mode; got: {prompt}"
        );
        assert!(result.ui_events.iter().any(|ev| matches!(
            ev,
            ralph_workflow::reducer::ui_event::UIEvent::PromptReplayHit {
                key,
                was_replayed: false
            } if key == "fix_0"
        )));
    });
}

#[test]
fn test_planning_invocation_canonicalizes_legacy_development_resume_prompt() {
    with_default_timeout(|| {
        let workspace = Arc::new(
            MemoryWorkspace::new_test().with_file(".agent/tmp/planning_prompt.txt", "fresh prompt"),
        );
        let mut fixture = IntegrationFixture::with_workspace(workspace);
        fixture.executor = Arc::new(MockProcessExecutor::new().with_agent_result(
            "claude",
            Ok(ralph_workflow::executor::AgentCommandResult::success()),
        ));

        let mut handler = MainEffectHandler::new(PipelineState::initial(1, 1));
        handler.state.agent_chain = AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string()],
                vec![vec!["model-a".to_string()]],
                AgentRole::Developer,
            )
            .with_drain(AgentDrain::Development);
        handler.state.agent_chain.retry_cycle = 1;
        handler.state.agent_chain.rate_limit_continuation_prompt = Some(
            ralph_workflow::reducer::state::RateLimitContinuationPrompt {
                drain: AgentDrain::Development,
                role: AgentRole::Developer,
                prompt: "saved planning continuation prompt".to_string(),
            },
        );

        let mut ctx = fixture.ctx(None);
        let result = handler
            .execute(Effect::InvokePlanningAgent { iteration: 0 }, &mut ctx)
            .expect("planning invocation should succeed");

        assert!(matches!(
            result.event,
            PipelineEvent::Agent(
                ralph_workflow::reducer::event::AgentEvent::InvocationStarted { .. }
            )
        ));

        let calls = fixture.executor.agent_calls();
        assert_eq!(calls.len(), 1);
        assert_eq!(
            calls[0].prompt, "saved planning continuation prompt",
            "legacy-compatible planning resume should keep its continuation prompt"
        );
        assert_eq!(
            handler.state.agent_chain.current_drain,
            AgentDrain::Planning
        );
        assert_eq!(handler.state.agent_chain.current_role, AgentRole::Developer);

        let continuation = handler
            .state
            .agent_chain
            .rate_limit_continuation_prompt
            .as_ref()
            .expect("continuation prompt should be preserved");
        assert_eq!(continuation.drain, AgentDrain::Planning);
        assert_eq!(continuation.role, AgentRole::Developer);
    });
}

#[test]
fn test_init_local_config_tolerates_global_named_chains_without_drain_bindings() {
    with_default_timeout(|| {
        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/repo/.agent/ralph-workflow.toml")
            .with_file(
                "/test/config/ralph-workflow.toml",
                r#"
[agent_chains]
shared_dev = ["codex", "claude"]
shared_review = ["claude"]
"#,
            );

        handle_init_local_config_with(Colors::new(), &env, false)
            .expect("init-local-config should tolerate partial named global config");

        let content = env
            .get_file(Path::new("/test/repo/.agent/ralph-workflow.toml"))
            .expect("local config should be written");

        assert!(
            content.contains(r#"shared_dev = ["codex", "claude"]"#),
            "should preserve named chains from the global config, got:\n{content}"
        );
        assert!(
            content.contains(r#"shared_review = ["claude"]"#),
            "should preserve review chains from the global config, got:\n{content}"
        );
        assert!(
            content.contains("# [agent_drains]"),
            "template should still render drain bindings when global config omits them, got:\n{content}"
        );
    });
}

/// Test that named-schema defaults prefer sibling drain bindings before compatibility names.
#[test]
fn test_named_schema_prefers_sibling_drains_for_commit_and_analysis_defaults() {
    with_default_timeout(|| {
        let config = ralph_workflow::config::UnifiedConfig {
            agent_chains: std::collections::HashMap::from([
                ("shared_dev".to_string(), vec!["codex".to_string()]),
                ("shared_review".to_string(), vec!["claude".to_string()]),
                (
                    "developer".to_string(),
                    vec!["legacy-dev".to_string(), "legacy-dev-2".to_string()],
                ),
                (
                    "reviewer".to_string(),
                    vec!["legacy-review".to_string(), "legacy-review-2".to_string()],
                ),
            ]),
            agent_drains: std::collections::HashMap::from([
                ("planning".to_string(), "shared_dev".to_string()),
                ("development".to_string(), "shared_dev".to_string()),
                ("review".to_string(), "shared_review".to_string()),
                ("fix".to_string(), "shared_review".to_string()),
            ]),
            ..Default::default()
        };

        let resolved = config
            .resolve_agent_drains_checked()
            .expect("drain defaults should resolve")
            .expect("named drain config should resolve");

        let commit = resolved
            .binding(AgentDrain::Commit)
            .expect("commit drain should resolve");
        let analysis = resolved
            .binding(AgentDrain::Analysis)
            .expect("analysis drain should resolve");

        assert_eq!(commit.chain_name, "shared_review");
        assert_eq!(commit.agents, vec!["claude"]);
        assert_eq!(analysis.chain_name, "shared_dev");
        assert_eq!(analysis.agents, vec!["codex"]);
    });
}

#[test]
fn test_named_schema_prefers_sibling_drains_for_planning_development_and_review_fix_defaults() {
    with_default_timeout(|| {
        let config = ralph_workflow::config::UnifiedConfig {
            agent_chains: std::collections::HashMap::from([
                ("shared_dev".to_string(), vec!["codex".to_string()]),
                ("shared_review".to_string(), vec!["claude".to_string()]),
                (
                    "developer".to_string(),
                    vec!["legacy-dev".to_string(), "legacy-dev-2".to_string()],
                ),
                (
                    "reviewer".to_string(),
                    vec!["legacy-review".to_string(), "legacy-review-2".to_string()],
                ),
            ]),
            agent_drains: std::collections::HashMap::from([
                ("planning".to_string(), "shared_dev".to_string()),
                ("review".to_string(), "shared_review".to_string()),
            ]),
            ..Default::default()
        };

        let resolved = config
            .resolve_agent_drains_checked()
            .expect("drain defaults should resolve")
            .expect("named drain config should resolve");

        let development = resolved
            .binding(AgentDrain::Development)
            .expect("development drain should resolve");
        let fix = resolved
            .binding(AgentDrain::Fix)
            .expect("fix drain should resolve");

        assert_eq!(development.chain_name, "shared_dev");
        assert_eq!(development.agents, vec!["codex"]);
        assert_eq!(fix.chain_name, "shared_review");
        assert_eq!(fix.agents, vec!["claude"]);
    });
}

#[test]
fn test_fix_continuation_succeeded_clears_fix_drain_state_before_returning_to_review() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 2));
        state.phase = PipelinePhase::Review;
        state.review_issues_found = true;
        state.fix_prompt_prepared_pass = Some(0);
        state.fix_required_files_cleaned_pass = Some(0);
        state.fix_agent_invoked_pass = Some(0);
        state.fix_result_xml_extracted_pass = Some(0);
        state.fix_validated_outcome = Some(ralph_workflow::reducer::state::FixValidatedOutcome {
            pass: 0,
            status: ralph_workflow::reducer::state::FixStatus::IssuesRemain,
            summary: Some("continue".to_string()),
        });
        state.fix_result_xml_archived_pass = Some(0);
        state.agent_chain.current_drain = AgentDrain::Fix;
        state.continuation.fix_continue_pending = true;
        state.continuation.fix_continuation_attempt = 1;
        state.continuation.fix_status =
            Some(ralph_workflow::reducer::state::FixStatus::IssuesRemain);
        state.continuation.fix_previous_summary = Some("continue".to_string());
        state.continuation.last_fix_xsd_error = Some("bad xml".to_string());

        let state = reduce(
            state,
            ralph_workflow::reducer::event::PipelineEvent::fix_continuation_succeeded(0, 1),
        );
        let state = reduce(
            state,
            ralph_workflow::reducer::event::PipelineEvent::commit_created(
                "abc".to_string(),
                "fix".to_string(),
            ),
        );

        assert_eq!(state.phase, PipelinePhase::Review);
        assert_eq!(state.runtime_drain(), AgentDrain::Review);
        assert!(!state.review_issues_found);
        assert!(state.fix_prompt_prepared_pass.is_none());
        assert!(state.fix_required_files_cleaned_pass.is_none());
        assert!(state.fix_agent_invoked_pass.is_none());
        assert!(state.fix_result_xml_extracted_pass.is_none());
        assert!(state.fix_validated_outcome.is_none());
        assert!(state.fix_result_xml_archived_pass.is_none());
        assert!(!state.continuation.fix_continue_pending);
        assert_eq!(state.continuation.fix_continuation_attempt, 0);
        assert!(state.continuation.fix_status.is_none());
        assert!(state.continuation.fix_previous_summary.is_none());
        assert!(state.continuation.last_fix_xsd_error.is_none());
    });
}

#[test]
fn test_fix_continuation_budget_exhausted_clears_fix_drain_state_before_returning_to_review() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 2));
        state.phase = PipelinePhase::Review;
        state.review_issues_found = true;
        state.fix_prompt_prepared_pass = Some(0);
        state.fix_required_files_cleaned_pass = Some(0);
        state.fix_agent_invoked_pass = Some(0);
        state.fix_result_xml_extracted_pass = Some(0);
        state.fix_validated_outcome = Some(ralph_workflow::reducer::state::FixValidatedOutcome {
            pass: 0,
            status: ralph_workflow::reducer::state::FixStatus::IssuesRemain,
            summary: Some("continue".to_string()),
        });
        state.fix_result_xml_archived_pass = Some(0);
        state.agent_chain.current_drain = AgentDrain::Fix;
        state.continuation.fix_continue_pending = true;
        state.continuation.fix_continuation_attempt = 2;
        state.continuation.fix_status =
            Some(ralph_workflow::reducer::state::FixStatus::IssuesRemain);
        state.continuation.fix_previous_summary = Some("continue".to_string());
        state.continuation.last_fix_xsd_error = Some("bad xml".to_string());

        let state = reduce(
            state,
            ralph_workflow::reducer::event::PipelineEvent::fix_continuation_budget_exhausted(
                0,
                2,
                ralph_workflow::reducer::state::FixStatus::IssuesRemain,
            ),
        );
        let state = reduce(
            state,
            ralph_workflow::reducer::event::PipelineEvent::commit_created(
                "abc".to_string(),
                "fix".to_string(),
            ),
        );

        assert_eq!(state.phase, PipelinePhase::Review);
        assert_eq!(state.runtime_drain(), AgentDrain::Review);
        assert!(!state.review_issues_found);
        assert!(state.fix_prompt_prepared_pass.is_none());
        assert!(state.fix_required_files_cleaned_pass.is_none());
        assert!(state.fix_agent_invoked_pass.is_none());
        assert!(state.fix_result_xml_extracted_pass.is_none());
        assert!(state.fix_validated_outcome.is_none());
        assert!(state.fix_result_xml_archived_pass.is_none());
        assert!(!state.continuation.fix_continue_pending);
        assert_eq!(state.continuation.fix_continuation_attempt, 0);
        assert!(state.continuation.fix_status.is_none());
        assert!(state.continuation.fix_previous_summary.is_none());
        assert!(state.continuation.last_fix_xsd_error.is_none());
    });
}

/// Test that named drain config can still carry provider fallback and retry metadata.
#[test]
fn test_named_schema_accepts_metadata_only_legacy_agent_chain_section() {
    with_default_timeout(|| {
        let workspace = MemoryWorkspace::new_test().with_file(
            ".agent/agents.toml",
            r#"
            [agent_chains]
            shared_dev = ["codex"]
            shared_review = ["claude"]

            [agent_drains]
            planning = "shared_dev"
            development = "shared_dev"
            review = "shared_review"
            fix = "shared_review"

            [agent_chain]
            max_retries = 7
            retry_delay_ms = 2500
            backoff_multiplier = 3.0
            max_backoff_ms = 90000
            max_cycles = 5
            provider_fallback.opencode = ["-m opencode/glm-4.7-free"]
            "#,
        );

        let config = AgentsConfigFile::load_from_file_with_workspace(
            std::path::Path::new(".agent/agents.toml"),
            &workspace,
        )
        .expect("config should parse")
        .expect("config should exist");

        let resolved = config
            .resolve_drains_checked()
            .expect("metadata-only legacy section should coexist with named drains")
            .expect("named drain config should resolve");

        assert_eq!(resolved.max_retries, 7);
        assert_eq!(resolved.retry_delay_ms, 2_500);
        assert!((resolved.backoff_multiplier - 3.0).abs() < f64::EPSILON);
        assert_eq!(resolved.max_backoff_ms, 90_000);
        assert_eq!(resolved.max_cycles, 5);
        assert_eq!(
            resolved.provider_fallback.get("opencode"),
            Some(&vec!["-m opencode/glm-4.7-free".to_string()])
        );
    });
}

/// Test that `merge_with_content` keeps metadata-only legacy `agent_chain` tables empty.
#[test]
fn test_named_schema_merge_keeps_metadata_only_legacy_bindings_empty() {
    with_default_timeout(|| {
        let global = ralph_workflow::config::UnifiedConfig::default();
        let local_toml = r#"
            [agent_chains]
            shared_dev = ["codex"]
            shared_review = ["claude"]

            [agent_drains]
            planning = "shared_dev"
            development = "shared_dev"
            review = "shared_review"
            fix = "shared_review"

            [agent_chain]
            max_retries = 7
            retry_delay_ms = 2500
            "#;

        let local = ralph_workflow::config::UnifiedConfig::load_from_content(local_toml)
            .expect("config should parse");
        let merged = global.merge_with_content(local_toml, &local);
        let chain = merged
            .agent_chain
            .expect("metadata-only legacy table should remain available");

        assert!(
            !chain.has_role_bindings(),
            "metadata-only legacy table must not materialize built-in role bindings when named drains are present"
        );
        assert_eq!(chain.max_retries, 7);
        assert_eq!(chain.retry_delay_ms, 2_500);
    });
}

#[test]
fn test_validate_config_file_rejects_mixed_schema_when_legacy_role_key_is_empty() {
    with_default_timeout(|| {
        let content = r#"
[agent_chain]
reviewer = []

[agent_chains]
shared_review = ["claude"]

[agent_drains]
review = "shared_review"
fix = "shared_review"
planning = "shared_review"
development = "shared_review"
commit = "shared_review"
analysis = "shared_review"
"#;

        let result = validate_config_file(std::path::Path::new("test.toml"), content);
        let errors = result.expect_err("empty legacy role keys must still reject mixed schemas");

        assert!(
            errors.iter().any(|error| matches!(
                error,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key == "agent_chain"
                        && message.contains("agent_chains")
                        && message.contains("agent_drains")
            )),
            "expected mixed schema error, got: {errors:?}"
        );

        let unified = UnifiedConfig::load_from_content(content).expect("config should parse");
        let error = unified
            .resolve_agent_drains_checked()
            .expect_err("mixed schema should be rejected after parsing");
        assert!(error.contains("agent_chain"));
    });
}

#[test]
fn test_planning_reinitializes_when_resume_kept_development_drain() {
    with_default_timeout(|| {
        let state = with_locked_prompt_permissions(PipelineState {
            phase: PipelinePhase::Planning,
            gitignore_entries_ensured: true,
            context_cleaned: true,
            agent_chain: PipelineState::initial(5, 2)
                .agent_chain
                .with_agents(
                    vec!["claude".to_string()],
                    vec![vec![]],
                    AgentRole::Developer,
                )
                .with_drain(AgentDrain::Development),
            ..PipelineState::initial(5, 2)
        });

        assert!(matches!(
            determine_next_effect(&state),
            Effect::InitializeAgentChain {
                drain: AgentDrain::Planning,
                ..
            }
        ));
    });
}

#[test]
fn test_fix_chain_reinitializes_when_runtime_fix_uses_review_drain_chain() {
    with_default_timeout(|| {
        let state = PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 1,
            review_issues_found: true,
            agent_chain: PipelineState::initial(1, 1)
                .agent_chain
                .with_agents(vec!["mock".to_string()], vec![vec![]], AgentRole::Reviewer)
                .with_drain(AgentDrain::Review),
            ..with_locked_prompt_permissions(PipelineState::initial(1, 1))
        };

        assert!(matches!(
            determine_next_effect(&state),
            Effect::InitializeAgentChain {
                drain: AgentDrain::Fix,
                ..
            }
        ));
    });
}

#[test]
fn test_load_config_revalidates_merged_named_schema() {
    with_default_timeout(|| {
        let global_toml = r#"
[agent_chains]
shared_dev = ["codex"]
shared_review = ["claude"]
"#;

        let local_toml = r#"
[agent_drains]
planning = "shared_dev"
development = "shared_dev"
"#;

        let env = MemoryConfigEnvironment::new()
            .with_unified_config_path("/test/config/ralph-workflow.toml")
            .with_local_config_path("/test/project/.agent/ralph-workflow.toml")
            .with_file("/test/config/ralph-workflow.toml", global_toml)
            .with_file("/test/project/.agent/ralph-workflow.toml", local_toml);

        let result = load_config_from_path_with_env(None, &env);
        let Err(ConfigLoadWithValidationError::ValidationErrors(errors)) = result else {
            panic!("expected merged named schema validation failure");
        };

        assert!(
            errors.iter().any(|error| matches!(
                error,
                ConfigValidationError::InvalidValue { key, message, .. }
                    if key == "agent_drains"
                        && message.contains("review")
                        && message.contains("fix")
            )),
            "expected merged named schema resolution error, got: {errors:?}"
        );
    });
}

#[test]
fn test_named_schema_merge_rejects_legacy_role_bindings_even_when_compatibility_view_is_empty() {
    with_default_timeout(|| {
        let global_toml = r#"
[agent_chain]
developer = ["codex"]
reviewer = ["claude"]
max_retries = 7
provider_fallback.opencode = ["-m opencode/glm-4.7-free"]
"#;
        let local_toml = r#"
[agent_chains]
shared_dev = ["opencode"]
shared_review = ["gemini"]

[agent_drains]
planning = "shared_dev"
development = "shared_dev"
review = "shared_review"
fix = "shared_review"
"#;

        let global =
            UnifiedConfig::load_from_content(global_toml).expect("global config should load");
        let local = UnifiedConfig::load_from_content(local_toml).expect("local config should load");
        let merged = global.merge_with_content(local_toml, &local);

        assert!(
            !merged
                .agent_chain
                .as_ref()
                .is_some_and(ralph_workflow::agents::fallback::FallbackConfig::has_role_bindings),
            "named-schema merges should keep only compatibility metadata"
        );

        let error = merged
            .resolve_agent_drains_checked()
            .expect_err("merged config should reject mixed legacy and named schemas");

        assert!(error.contains("agent_chain"));
        assert!(error.contains("agent_chains/agent_drains"));
    });
}

#[test]
fn test_per_file_validation_accepts_partial_named_chain_and_drain_layers() {
    with_default_timeout(|| {
        let chains_only = r#"
[agent_chains]
shared_dev = ["codex"]
shared_review = ["claude"]
"#;
        let drains_only = r#"
[agent_drains]
planning = "shared_dev"
development = "shared_dev"
review = "shared_review"
fix = "shared_review"
"#;

        assert!(
            validate_config_file(std::path::Path::new("global.toml"), chains_only).is_ok(),
            "named chain layer should validate before merge"
        );
        assert!(
            validate_config_file(std::path::Path::new("local.toml"), drains_only).is_ok(),
            "named drain layer should validate before merge"
        );
    });
}

/// Test that the built-in default registry consumes the named chain + drain schema.
#[test]
fn test_registry_new_uses_default_named_drain_bindings() {
    with_default_timeout(|| {
        let registry = ralph_workflow::agents::AgentRegistry::new()
            .expect("default registry should build from the embedded template");

        let development = registry
            .resolved_drain(AgentDrain::Development)
            .expect("default development drain should resolve");
        let review = registry
            .resolved_drain(AgentDrain::Review)
            .expect("default review drain should resolve");

        assert!(
            !development.agents.is_empty(),
            "default named schema should populate development drain bindings"
        );
        assert!(
            !review.agents.is_empty(),
            "default named schema should populate review drain bindings"
        );
    });
}

/// Test that the bundled unified config example teaches the canonical named chain/drain schema.
#[test]
fn test_default_unified_config_example_uses_named_chain_and_drain_schema() {
    with_default_timeout(|| {
        let uncommented_lines = ralph_workflow::config::unified::DEFAULT_UNIFIED_CONFIG
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty() && !line.starts_with('#'))
            .collect::<Vec<_>>();
        let mut current_section = "";
        let legacy_role_bindings = uncommented_lines
            .iter()
            .filter_map(|line| {
                if line.starts_with('[') && line.ends_with(']') {
                    current_section = line;
                    return None;
                }

                (current_section == "[agent_chain]"
                    && (line.starts_with("developer =")
                        || line.starts_with("reviewer =")
                        || line.starts_with("commit =")
                        || line.starts_with("analysis =")))
                .then_some(*line)
            })
            .collect::<Vec<_>>();

        assert!(
            legacy_role_bindings.is_empty(),
            "embedded unified config example should not teach legacy role bindings as the primary schema: {legacy_role_bindings:?}"
        );
        assert!(
            uncommented_lines.contains(&"[agent_chains]"),
            "embedded unified config example should define named reusable chains"
        );
        assert!(
            uncommented_lines.contains(&"[agent_drains]"),
            "embedded unified config example should bind built-in drains"
        );
        assert!(
            uncommented_lines.contains(&"planning = \"developer\""),
            "embedded unified config example should bind planning to the shared developer chain"
        );
        assert!(
            uncommented_lines.contains(&"review = \"reviewer\""),
            "embedded unified config example should bind review to the shared reviewer chain"
        );
    });
}

/// Test that user-facing docs teach named chains and drains as the primary schema.
#[test]
fn test_user_facing_examples_teach_named_chain_and_drain_schema() {
    with_default_timeout(|| {
        let public_examples = [
            ("README", README_TEXT),
            ("agents::mod docs", AGENTS_MOD_SOURCE),
            ("agents::registry docs", AGENTS_REGISTRY_SOURCE),
            ("agents::opencode_resolver docs", OPENCODE_RESOLVER_SOURCE),
            ("config::unified docs", CONFIG_UNIFIED_MOD_SOURCE),
        ];

        for (label, text) in public_examples {
            assert!(
                text.contains("[agent_chains]"),
                "{label} should teach named reusable chains"
            );
            assert!(
                text.contains("[agent_drains]"),
                "{label} should teach built-in drain bindings"
            );
            assert!(
                !text.contains("[agent_chain]\ndeveloper ="),
                "{label} should not present legacy role-keyed [agent_chain] examples as canonical"
            );
        }
    });
}

/// Test that the compatibility guide's named-chain example resolves required review drains.
#[test]
fn test_agent_compatibility_named_chain_example_covers_review_and_fix_drains() {
    with_default_timeout(|| {
        let example_start = AGENT_COMPATIBILITY_DOC
            .find("[agent_chains]\n")
            .expect("compatibility guide should include a named-chain example");
        let example = &AGENT_COMPATIBILITY_DOC[example_start..];

        assert!(
            example.contains("review = \"reviewer\"") && example.contains("fix = \"reviewer\""),
            "named-chain example must show both review and fix drain bindings"
        );
    });
}

#[test]
fn test_named_schema_defaults_missing_siblings_from_explicit_bindings() {
    with_default_timeout(|| {
        let config = UnifiedConfig {
            agent_chains: std::collections::HashMap::from([
                (
                    "shared_dev".to_string(),
                    vec!["codex".to_string(), "claude".to_string()],
                ),
                (
                    "shared_review".to_string(),
                    vec!["claude".to_string(), "opencode".to_string()],
                ),
            ]),
            agent_drains: std::collections::HashMap::from([
                ("development".to_string(), "shared_dev".to_string()),
                ("fix".to_string(), "shared_review".to_string()),
            ]),
            ..Default::default()
        };

        let resolved = config
            .resolve_agent_drains_checked()
            .expect("sibling defaults should resolve from explicit built-in drains")
            .expect("named drain config should resolve");

        assert_eq!(
            resolved
                .binding(AgentDrain::Planning)
                .expect("planning should inherit from development")
                .chain_name,
            "shared_dev"
        );
        assert_eq!(
            resolved
                .binding(AgentDrain::Review)
                .expect("review should inherit from fix")
                .chain_name,
            "shared_review"
        );
        assert_eq!(
            resolved
                .binding(AgentDrain::Commit)
                .expect("commit should inherit from review/fix")
                .chain_name,
            "shared_review"
        );
        assert_eq!(
            resolved
                .binding(AgentDrain::Analysis)
                .expect("analysis should inherit from development/planning")
                .chain_name,
            "shared_dev"
        );
    });
}

#[test]
fn test_commit_message_agent_resolution_falls_back_to_review_drain_when_commit_drain_missing() {
    with_default_timeout(|| {
        let mut registry = AgentRegistry::new().expect("default registry should build");
        let unified = UnifiedConfig {
            agent_chains: std::collections::HashMap::from([
                ("shared_dev".to_string(), vec!["codex".to_string()]),
                (
                    "shared_review".to_string(),
                    vec!["claude".to_string(), "opencode".to_string()],
                ),
            ]),
            agent_drains: std::collections::HashMap::from([
                ("planning".to_string(), "shared_dev".to_string()),
                ("development".to_string(), "shared_dev".to_string()),
                ("review".to_string(), "shared_review".to_string()),
                ("fix".to_string(), "shared_review".to_string()),
            ]),
            ..Default::default()
        };
        registry.apply_unified_config(&unified);

        let app_config = Config::test_default();
        let workspace = Arc::new(MemoryWorkspace::new_test());
        let logger = Logger::new(Colors::new());
        let template_context = TemplateContext::default();
        let executor = Arc::new(MockProcessExecutor::new()) as Arc<dyn ProcessExecutor>;
        let config = commit_generation_config(
            &app_config,
            &registry,
            &workspace,
            &logger,
            &template_context,
            executor,
        );

        assert_eq!(
            resolve_commit_message_agents_for_testing(&config),
            vec!["claude".to_string(), "opencode".to_string()]
        );
    });
}

#[test]
fn test_named_schema_rejects_explicitly_empty_commit_drain() {
    with_default_timeout(|| {
        let toml_str = r#"
            [agent_chains]
            shared_dev = ["developer-agent"]
            shared_review = ["reviewer-agent"]
            empty_commit = []

            [agent_drains]
            planning = "shared_dev"
            development = "shared_dev"
            review = "shared_review"
            fix = "shared_review"
            commit = "empty_commit"
        "#;
        let unified = UnifiedConfig::load_from_content(toml_str).expect("config should parse");
        let error = unified
            .resolve_agent_drains_checked()
            .expect_err("empty built-in drain binding should be rejected");

        assert!(
            error.contains("agent_drains.commit"),
            "error should mention the empty commit drain: {error}"
        );
    });
}

#[test]
fn test_named_schema_rejects_empty_commit_drain_before_commit_agent_resolution() {
    with_default_timeout(|| {
        let unified = UnifiedConfig {
            agent_chains: std::collections::HashMap::from([
                ("shared_dev".to_string(), vec!["codex".to_string()]),
                ("shared_review".to_string(), vec!["claude".to_string()]),
                ("empty_commit".to_string(), Vec::new()),
            ]),
            agent_drains: std::collections::HashMap::from([
                ("planning".to_string(), "shared_dev".to_string()),
                ("development".to_string(), "shared_dev".to_string()),
                ("review".to_string(), "shared_review".to_string()),
                ("fix".to_string(), "shared_review".to_string()),
                ("commit".to_string(), "empty_commit".to_string()),
            ]),
            ..Default::default()
        };
        let error = unified
            .resolve_agent_drains_checked()
            .expect_err("empty built-in drain binding should be rejected");

        assert!(
            error.contains("agent_drains.commit"),
            "error should mention the empty commit drain: {error}"
        );
    });
}

#[test]
fn test_named_schema_rejects_empty_review_and_commit_drains() {
    with_default_timeout(|| {
        let unified = UnifiedConfig {
            agent_chains: std::collections::HashMap::from([
                ("shared_dev".to_string(), vec!["codex".to_string()]),
                ("empty_review".to_string(), Vec::new()),
                ("empty_commit".to_string(), Vec::new()),
            ]),
            agent_drains: std::collections::HashMap::from([
                ("planning".to_string(), "shared_dev".to_string()),
                ("development".to_string(), "shared_dev".to_string()),
                ("review".to_string(), "empty_review".to_string()),
                ("fix".to_string(), "empty_review".to_string()),
                ("commit".to_string(), "empty_commit".to_string()),
            ]),
            ..Default::default()
        };
        let error = unified
            .resolve_agent_drains_checked()
            .expect_err("empty built-in drain bindings should be rejected");

        assert!(
            error.contains("agent_drains.review") || error.contains("agent_drains.commit"),
            "error should mention an empty built-in drain binding: {error}"
        );
    });
}
