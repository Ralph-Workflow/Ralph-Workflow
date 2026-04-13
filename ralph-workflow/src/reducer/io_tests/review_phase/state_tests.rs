// Tests for state management during review
//
// These tests validate:
// - Agent chain clearing on phase transition
// - Agent chain initialization for review
// - Auth failure handling
// - Event loop state consistency
// - Complete flow from development through review

use crate::common::domain_types::AgentName;

fn agent_names_from_strings(strings: &[String]) -> Vec<AgentName> {
    strings.iter().cloned().map(AgentName::from).collect()
}

// Agent chain clearing on phase transition tests (BUG: agent chain not cleared)

/// Test that verifies the agent chain is cleared when transitioning from Development
/// to Review phase via `CommitCreated`.
///
/// This is a regression test for the bug where the developer agent chain was carried
/// over to the Review phase, causing the wrong agent to be used for review.
#[test]
fn test_commit_created_clears_agent_chain_when_transitioning_to_review() {
    use crate::reducer::orchestration::determine_next_effect;

    // Setup: state in Development phase with developer agent chain
    let developer_chain = crate::reducer::state::AgentChainState::initial()
        .with_agents(
            vec!["dev-agent-1".to_string(), "dev-agent-2".to_string()],
            vec![vec![], vec![]],
            crate::agents::AgentRole::Developer,
        )
        .with_max_cycles(3);

    let mut state = PipelineState {
        phase: PipelinePhase::Development,
        previous_phase: Some(PipelinePhase::Development),
        iteration: 4, // Last iteration (total is 5, so 4 is index of 5th iteration)
        total_iterations: 5,
        total_reviewer_passes: 2,
        agent_chain: developer_chain,
        commit: CommitState::Generated {
            message: "test commit".to_string(),
        },
        ..create_test_state()
    };

    // Verify developer chain is populated
    assert!(!state.agent_chain.agents.is_empty());
    assert_eq!(
        state.agent_chain.current_agent(),
        Some(&"dev-agent-1".to_string())
    );
    assert_eq!(
        state.agent_chain.current_role,
        crate::agents::AgentRole::Developer
    );

    // Simulate commit created after last development iteration
    state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "test commit".to_string()),
    );

    // After commit on last iteration, should transition to Review phase
    assert_eq!(
        state.phase,
        PipelinePhase::Review,
        "Should transition to Review phase after last development iteration commit"
    );

    // CRITICAL: The agent chain should be cleared so orchestration emits InitializeAgentChain
    assert!(
        state.agent_chain.agents.is_empty(),
        "Agent chain should be cleared when transitioning from Development to Review"
    );

    // Orchestration should now emit InitializeAgentChain for Reviewer
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::InitializeAgentChain {
                drain: crate::agents::AgentDrain::Review,
                ..
            }
        ),
        "Orchestration should emit InitializeAgentChain for Reviewer, got {effect:?}"
    );
}

/// Test that orchestration uses the correct agent from `state.agent_chain` for review,
/// not `ctx.reviewer_agent`.
///
/// This test simulates the full flow:
/// 1. Initialize reviewer agent chain with specific agents
/// 2. Verify that `PrepareReviewContext` is emitted (not `InitializeAgentChain`)
/// 3. Verify the first agent in the chain is used (not a fallback)
#[test]
fn test_review_uses_agent_from_state_chain_not_context() {
    use crate::reducer::orchestration::determine_next_effect;

    // Setup: state in Review phase with reviewer agent chain already initialized
    let reviewer_chain = crate::reducer::state::AgentChainState::initial()
        .with_agents(
            vec![
                "codex".to_string(),
                "opencode".to_string(),
                "claude".to_string(),
            ],
            vec![vec![], vec![], vec![]],
            crate::agents::AgentRole::Reviewer,
        )
        .with_max_cycles(3);

    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        agent_chain: reviewer_chain,
        ..create_test_state()
    };

    // Verify chain is populated with correct first agent
    assert!(!state.agent_chain.agents.is_empty());
    assert_eq!(
        state.agent_chain.current_agent(),
        Some(&"codex".to_string()),
        "First agent should be 'codex' (from fallback chain), not 'claude'"
    );
    assert_eq!(state.agent_chain.current_agent_index, 0);

    // Orchestration should begin the single-task review chain (since chain is populated)
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::PrepareReviewContext { pass: 0 }
        ),
        "Orchestration should emit PrepareReviewContext, got {effect:?}"
    );
}

#[test]
fn test_fix_attempt_reinitializes_chain_for_reviewer_role() {
    use crate::reducer::orchestration::determine_next_effect;

    // Simulate the regression scenario: a stale development chain is present, but reducer-owned
    // drain state says review is entering fix flow. Orchestration must honor the explicit fix
    // drain and reinitialize that consumer instead of inferring from the stale chain contents.
    let developer_chain = crate::reducer::state::AgentChainState::initial().with_agents(
        vec!["dev-1".to_string(), "dev-2".to_string()],
        vec![vec![], vec![]],
        crate::agents::AgentRole::Developer,
    );

    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 1,
        review_issues_found: true,
        agent_chain: developer_chain.with_drain(crate::agents::AgentDrain::Fix),
        ..create_test_state()
    };

    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::PrepareFixPrompt {
                pass: 0,
                prompt_mode: crate::reducer::state::PromptMode::Normal,
            }
        ),
        "Expected fix prompt preparation for explicit Fix drain, got {effect:?}"
    );
}

/// Test that auth failure during review advances the agent chain via events.
#[test]
fn test_auth_failure_during_review_advances_agent_chain() {
    use crate::reducer::event::AgentErrorKind;

    // Setup: state in Review phase with reviewer agent chain
    let reviewer_chain = crate::reducer::state::AgentChainState::initial()
        .with_agents(
            vec![
                "codex".to_string(),
                "opencode".to_string(),
                "claude".to_string(),
            ],
            vec![vec![], vec![], vec![]],
            crate::agents::AgentRole::Reviewer,
        )
        .with_max_cycles(3);

    let mut state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        agent_chain: reviewer_chain,
        ..create_test_state()
    };

    // Verify starting with first agent
    assert_eq!(
        state.agent_chain.current_agent(),
        Some(&"codex".to_string())
    );
    assert_eq!(state.agent_chain.current_agent_index, 0);

    // Simulate auth failure - should advance to next agent
    state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            crate::agents::AgentRole::Reviewer,
            AgentName::from("codex"),
            1,
            AgentErrorKind::Authentication,
            false, // Not retriable - switch to next agent
        ),
    );

    // Should have advanced to next agent
    assert_eq!(
        state.agent_chain.current_agent(),
        Some(&"opencode".to_string()),
        "Should advance to next agent after auth failure"
    );
    assert_eq!(state.agent_chain.current_agent_index, 1);

    // Simulate another auth failure
    state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            crate::agents::AgentRole::Reviewer,
            AgentName::from("opencode"),
            1,
            AgentErrorKind::Authentication,
            false,
        ),
    );

    // Should have advanced to third agent
    assert_eq!(
        state.agent_chain.current_agent(),
        Some(&"claude".to_string()),
        "Should advance to third agent after second auth failure"
    );
    assert_eq!(state.agent_chain.current_agent_index, 2);
}

/// Test that after `ChainInitialized`, the handler can read the correct agent from state.
///
/// This test simulates what the handler does when calling `run_review_pass`:
/// it reads `state.agent_chain.current_agent()` to get the active reviewer agent.
#[test]
fn test_handler_reads_correct_agent_from_state_after_chain_initialized() {
    // Simulate the state after ChainInitialized event is processed
    let reviewers = vec![
        "codex".to_string(),
        "opencode".to_string(),
        "claude".to_string(),
    ];
    let state = reduce(
        PipelineState {
            phase: PipelinePhase::Review,
            reviewer_pass: 0,
            total_reviewer_passes: 2,
            ..create_test_state()
        },
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            agent_names_from_strings(&reviewers),
            vec![],
            3,
            1000,
            2.0,
            60000,
        ),
    );

    // This is what the handler does: read current_agent() and pass it to run_review_pass
    let review_agent = state.agent_chain.current_agent().cloned();

    // CRITICAL: review_agent must be Some("codex"), NOT None
    assert!(
        review_agent.is_some(),
        "Handler should get Some(agent) from state.agent_chain.current_agent(), got None"
    );
    assert_eq!(
        review_agent,
        Some("codex".to_string()),
        "Handler should pass 'codex' to run_review_pass, not '{review_agent:?}'"
    );

    // Verify the chain is properly populated
    assert_eq!(state.agent_chain.agents.len(), 3);
    assert_eq!(state.agent_chain.current_agent_index, 0);
}

/// Test that the full pipeline flow uses the correct reviewer agent order.
///
/// This is an end-to-end test of the Development -> Review transition to verify
/// the reviewer agent chain is properly initialized.
#[test]
fn test_full_pipeline_flow_uses_correct_reviewer_agent() {
    use crate::reducer::orchestration::determine_next_effect;

    // Start with a state that simulates post-development with dev agent chain
    let dev_chain = crate::reducer::state::AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string()], // Developer uses "claude"
            vec![vec![]],
            crate::agents::AgentRole::Developer,
        )
        .with_max_cycles(3);

    let mut state = PipelineState {
        phase: PipelinePhase::Development,
        previous_phase: Some(PipelinePhase::Development),
        iteration: 4, // Last iteration
        total_iterations: 5,
        total_reviewer_passes: 2,
        agent_chain: dev_chain,
        commit: CommitState::Generated {
            message: "test".to_string(),
        },
        ..create_test_state()
    };

    // Create commit to transition to Review
    state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "test".to_string()),
    );
    assert_eq!(state.phase, PipelinePhase::Review);

    // Orchestration should request agent chain initialization
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::InitializeAgentChain {
                drain: crate::agents::AgentDrain::Review,
                ..
            }
        ),
        "Should request reviewer chain initialization, got {effect:?}"
    );

    // Simulate initializing the reviewer chain with different agents
    let review_agents = vec![
        "codex".to_string(),
        "opencode".to_string(),
        "claude".to_string(),
    ];
    state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            agent_names_from_strings(&review_agents),
            vec![],
            3,
            1000,
            2.0,
            60000,
        ),
    );

    // Verify reviewer chain is now populated with correct agents
    assert_eq!(
        state.agent_chain.current_agent(),
        Some(&"codex".to_string()),
        "First reviewer agent should be 'codex', not 'claude'"
    );
    assert_eq!(
        state.agent_chain.current_role,
        crate::agents::AgentRole::Reviewer
    );

    // Now orchestration should emit PrepareReviewContext
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::PrepareReviewContext { pass: 0 }
        ),
        "Should emit PrepareReviewContext, got {effect:?}"
    );
}

/// Test that simulates the exact event loop behavior to verify handler state consistency.
///
/// This test simulates:
/// 1. State after `ChainInitialized` is processed and stored in handler
/// 2. Orchestration returns `PrepareReviewContext`
/// 3. Handler reads `current_agent()` from its state
///
/// The handler should have the updated state with populated agent chain.
#[test]
fn test_event_loop_state_consistency_for_review_agent() {
    use crate::reducer::orchestration::determine_next_effect;

    // === ITERATION N: InitializeAgentChain ===
    // State before InitializeAgentChain effect.
    // agent_chain is explicitly cleared to simulate the state after dev->review transition,
    // which clears the developer chain before the reviewer chain is initialized.
    let mut state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        agent_chain: crate::reducer::state::AgentChainState::initial(),
        ..create_test_state()
    };

    // Verify agent chain is empty (as it would be after dev->review transition)
    assert!(
        state.agent_chain.agents.is_empty(),
        "Agent chain should be empty before initialization"
    );

    // Orchestration should emit InitializeAgentChain
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::InitializeAgentChain {
                drain: crate::agents::AgentDrain::Review,
                ..
            }
        ),
        "Expected InitializeAgentChain, got {effect:?}"
    );

    // Handler executes InitializeAgentChain, emits ChainInitialized event
    // (simulating what handler.initialize_agent_chain does)
    let review_agents = vec![
        "codex".to_string(),
        "opencode".to_string(),
        "claude".to_string(),
    ];
    let event = PipelineEvent::agent_chain_initialized(
        crate::agents::AgentDrain::Review,
        agent_names_from_strings(&review_agents),
        vec![],
        3,
        1000,
        2.0,
        60000,
    );

    // Event loop: reduce state with the event
    state = reduce(state, event);

    // Event loop: handler.state = new_state.clone() (simulating event loop line 194)
    // In real code, handler.state would be updated here
    let handler_state = state.clone();

    // === ITERATION N+1: PrepareReviewContext ===
    // Orchestration determines next effect based on updated state
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::PrepareReviewContext { pass: 0 }
        ),
        "Expected PrepareReviewContext, got {effect:?}"
    );

    // Simulate context prepared
    state = reduce(state, PipelineEvent::review_context_prepared(0));

    // Next: MaterializeReviewInputs
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::MaterializeReviewInputs { pass: 0 }
        ),
        "Expected MaterializeReviewInputs, got {effect:?}"
    );

    let sig = state.agent_chain.consumer_signature_sha256();
    state = reduce(
        state,
        PipelineEvent::review_inputs_materialized(
            0,
            crate::reducer::state::MaterializedPromptInput {
                kind: crate::reducer::state::PromptInputKind::Plan,
                content_id_sha256: "id".to_string(),
                consumer_signature_sha256: sig.clone(),
                original_bytes: 1,
                final_bytes: 1,
                model_budget_bytes: None,
                inline_budget_bytes: None,
                representation: crate::reducer::state::PromptInputRepresentation::Inline,
                reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
            },
            crate::reducer::state::MaterializedPromptInput {
                kind: crate::reducer::state::PromptInputKind::Diff,
                content_id_sha256: "id".to_string(),
                consumer_signature_sha256: sig,
                original_bytes: 1,
                final_bytes: 1,
                model_budget_bytes: None,
                inline_budget_bytes: None,
                representation: crate::reducer::state::PromptInputRepresentation::Inline,
                reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
            },
        ),
    );

    // Next: PrepareReviewPrompt
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::PrepareReviewPrompt { pass: 0, .. }
        ),
        "Expected PrepareReviewPrompt, got {effect:?}"
    );

    // Simulate prompt prepared
    state = reduce(state, PipelineEvent::review_prompt_prepared(0));

    // Next: CleanupRequiredFiles (for issues.xml)
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::CleanupRequiredFiles { ref files }
                if files.iter().any(|f| f.contains("issues.xml"))
        ),
        "Expected CleanupRequiredFiles for issues.xml, got {effect:?}"
    );

    // Simulate cleanup
    state = reduce(state, PipelineEvent::review_issues_xml_cleaned(0));

    // Next: InvokeReviewAgent
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::InvokeReviewAgent { pass: 0 }
        ),
        "Expected InvokeReviewAgent, got {effect:?}"
    );

    // Handler executes InvokeReviewAgent, reads current_agent from its state
    // This is exactly what handler.invoke_review_agent does
    let review_agent = handler_state.agent_chain.current_agent().cloned();

    // CRITICAL ASSERTION: review_agent must be Some("codex")
    assert!(
        review_agent.is_some(),
        "Handler should get Some(agent) from state.agent_chain.current_agent(), got None. \
        This means the agent chain was not properly populated before InvokeReviewAgent."
    );
    assert_eq!(
        review_agent,
        Some("codex".to_string()),
        "Handler should pass 'codex' to InvokeReviewAgent, got {review_agent:?}. \
        This means the wrong agent is being used."
    );

    // Verify chain state is correct
    assert_eq!(handler_state.agent_chain.agents.len(), 3);
    assert_eq!(handler_state.agent_chain.current_agent_index, 0);
    assert_eq!(
        handler_state.agent_chain.current_role,
        crate::agents::AgentRole::Reviewer
    );
}

/// Full integration test: Development -> Review (Phase 2 flow)
///
/// This test simulates the complete flow from development to review phase,
/// verifying that the agent chain is correctly initialized for the reviewer.
///
/// Phase 2: Development routes directly to Review (not via CommitMessage first).
/// The commit checkpoint happens after the review/fix cycle completes.
#[test]
fn test_complete_flow_dev_commit_review_uses_correct_reviewer_agent() {
    use crate::reducer::orchestration::determine_next_effect;

    // Start with development phase, last iteration, with developer agent chain
    let dev_chain = crate::reducer::state::AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string()], // Developer uses "claude"
            vec![vec![]],
            crate::agents::AgentRole::Developer,
        )
        .with_max_cycles(3);

    let mut state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 4, // Last iteration (0-indexed, total is 5)
        total_iterations: 5,
        total_reviewer_passes: 2,
        agent_chain: dev_chain,
        ..create_test_state()
    };

    // === STEP 1: Development completes successfully ===
    // Phase 2: Development routes directly to Review (not CommitMessage first)
    state = reduce(
        state,
        PipelineEvent::development_iteration_completed(4, true),
    );
    assert_eq!(
        state.phase,
        PipelinePhase::Review,
        "Phase 2: Should transition to Review after successful dev iteration"
    );
    assert_eq!(
        state.previous_phase,
        Some(PipelinePhase::Development),
        "previous_phase should be Development"
    );

    // CRITICAL: Agent chain should be CLEARED to force reinitialization
    assert!(
        state.agent_chain.agents.is_empty(),
        "Agent chain should be empty after dev->review transition, got {:?}",
        state.agent_chain.agents
    );

    // === STEP 2: Orchestration cleans continuation context if needed ===
    let mut effect = determine_next_effect(&state);
    if matches!(
        effect,
        crate::reducer::effect::Effect::CleanupContinuationContext
    ) {
        state = reduce(
            state,
            PipelineEvent::development_continuation_context_cleaned(),
        );
        effect = determine_next_effect(&state);
    }

    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::InitializeAgentChain {
                drain: crate::agents::AgentDrain::Review,
                ..
            }
        ),
        "Orchestration should request reviewer chain initialization, got {effect:?}"
    );

    // === STEP 3: Agent chain initialized with reviewer agents ===
    let last_reviewer_agents = vec![
        "codex".to_string(),
        "opencode".to_string(),
        "claude".to_string(),
    ];
    state = reduce(
        state,
        PipelineEvent::agent_chain_initialized(
            crate::agents::AgentDrain::Review,
            agent_names_from_strings(&last_reviewer_agents),
            vec![],
            3,
            1000,
            2.0,
            60000,
        ),
    );

    // Verify reviewer chain is populated
    assert!(!state.agent_chain.agents.is_empty());
    assert_eq!(
        state.agent_chain.current_agent(),
        Some(&"codex".to_string()),
        "First reviewer agent should be 'codex'"
    );
    assert_eq!(
        state.agent_chain.current_role,
        crate::agents::AgentRole::Reviewer
    );

    // === STEP 4: Orchestration begins single-task review chain ===
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::PrepareReviewContext { pass: 0 }
        ),
        "Should request PrepareReviewContext, got {effect:?}"
    );

    // Simulate context + prompt prepared, then cleanup before invoking agent
    state = reduce(state, PipelineEvent::review_context_prepared(0));
    let sig = state.agent_chain.consumer_signature_sha256();
    state = reduce(
        state,
        PipelineEvent::review_inputs_materialized(
            0,
            crate::reducer::state::MaterializedPromptInput {
                kind: crate::reducer::state::PromptInputKind::Plan,
                content_id_sha256: "id".to_string(),
                consumer_signature_sha256: sig.clone(),
                original_bytes: 1,
                final_bytes: 1,
                model_budget_bytes: None,
                inline_budget_bytes: None,
                representation: crate::reducer::state::PromptInputRepresentation::Inline,
                reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
            },
            crate::reducer::state::MaterializedPromptInput {
                kind: crate::reducer::state::PromptInputKind::Diff,
                content_id_sha256: "id".to_string(),
                consumer_signature_sha256: sig,
                original_bytes: 1,
                final_bytes: 1,
                model_budget_bytes: None,
                inline_budget_bytes: None,
                representation: crate::reducer::state::PromptInputRepresentation::Inline,
                reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
            },
        ),
    );
    state = reduce(state, PipelineEvent::review_prompt_prepared(0));
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::CleanupRequiredFiles { ref files }
                if files.iter().any(|f| f.contains("issues.xml"))
        ),
        "Should request CleanupRequiredFiles for issues.xml, got {effect:?}"
    );
    state = reduce(state, PipelineEvent::review_issues_xml_cleaned(0));
    let effect = determine_next_effect(&state);
    assert!(
        matches!(
            effect,
            crate::reducer::effect::Effect::InvokeReviewAgent { pass: 0 }
        ),
        "Should request InvokeReviewAgent, got {effect:?}"
    );

    // === STEP 5: Simulate what handler does ===
    // Handler reads current_agent from state to pass to run_review_pass
    let review_agent = state.agent_chain.current_agent().cloned();
    assert_eq!(
        review_agent,
        Some("codex".to_string()),
        "Handler should pass 'codex' to run_review_pass"
    );
}
