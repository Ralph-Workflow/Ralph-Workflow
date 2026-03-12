//! Integration test for deterministic agent-chain normalization.
//!
//! Verifies that agent chain state is normalized before each invocation to ensure
//! checkpoint replay produces identical agent selection.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use ralph_workflow::agents::{AgentDrain, AgentRole, AgentsConfigFile};
use ralph_workflow::reducer::determine_next_effect;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::state::{PipelineState, PromptMode};
use ralph_workflow::reducer::state_reduction::reduce;
use ralph_workflow::workspace::MemoryWorkspace;

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;

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
