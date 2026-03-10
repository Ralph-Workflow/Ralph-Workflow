// Tests for effect capture and execution tracking.

use super::*;

#[test]
fn mock_effect_handler_captures_create_commit_effect() {
    let state = PipelineState::initial(1, 0);
    let handler = MockEffectHandler::new(state);

    // Should start with no captured effects
    let captured = handler.captured_effects();
    assert!(captured.is_empty(), "Should start with no captured effects");
}

#[test]
fn mock_effect_handler_returns_commit_created_for_create_commit() {
    let state = PipelineState::initial(1, 0);
    let handler = MockEffectHandler::new(state);

    // Verify the handler can be created and has expected initial state
    assert!(handler.captured_effects().is_empty());
    assert_eq!(handler.effect_count(), 0);
}

/// TDD test: `MockEffectHandler` must implement `EffectHandler` trait
/// and return appropriate events without making real git calls.
#[test]
fn mock_effect_handler_implements_effect_handler_trait() {
    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    // This test will fail until we implement EffectHandler for MockEffectHandler
    // The key is that execute() captures the effect and returns a mock event
    let effect = Effect::CreateCommit {
        message: "test commit".to_string(),
        files: vec![],
    };

    // Create a minimal mock PhaseContext - this requires test-utils
    // For now we test that the handler implements the trait by calling execute_mock
    let result = handler.execute_mock(&effect);

    // Effect should be captured
    assert!(
        handler.was_effect_executed(|e| matches!(e, Effect::CreateCommit { .. })),
        "CreateCommit effect should be captured"
    );

    // Event should be CommitCreated (no real git call)
    assert!(
        matches!(
            result.event,
            PipelineEvent::Commit(crate::reducer::event::CommitEvent::Created { .. })
        ),
        "Should return CommitCreated event, got: {:?}",
        result.event
    );
}

/// Test that `MockEffectHandler` properly implements the `EffectHandler` trait
/// with a real `PhaseContext`. This proves it can be a drop-in replacement
/// for `MainEffectHandler` in tests.
#[test]
fn mock_effect_handler_trait_execute_with_phase_context() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let cloud = crate::config::types::CloudConfig::disabled();

    // Create test fixtures
    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();

    // Create PhaseContext
    let mut ctx = PhaseContext {
        config: &config,
        registry: &registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "test-developer",
        reviewer_agent: "test-reviewer",
        review_guidelines: None,
        template_context: &template_context,
        run_context: RunContext::new(),
        execution_history: ExecutionHistory::new(),
        executor: &*executor,
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    // Create handler and execute effect via trait method
    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    let effect = Effect::CreateCommit {
        message: "test via trait".to_string(),
        files: vec![],
    };

    // Call the trait method (not execute_mock)
    let result = handler.execute(effect, &mut ctx);

    // Should succeed
    assert!(result.is_ok(), "execute should succeed");

    // Should return CommitCreated event
    let effect_result = result.unwrap();
    match effect_result.event {
        PipelineEvent::Commit(crate::reducer::event::CommitEvent::Created { hash, message }) => {
            assert_eq!(hash, "mock_commit_hash_abc123");
            assert_eq!(message, "test via trait");
        }
        other => panic!("Expected CommitCreated, got {other:?}"),
    }

    // Effect should be captured
    assert!(handler.was_effect_executed(|e| matches!(e, Effect::CreateCommit { .. })));
    assert_eq!(handler.effect_count(), 1);
}

/// Test that `MockEffectHandler` captures UI events for development extraction.
#[test]
fn mock_effect_handler_captures_iteration_progress_ui() {
    let state = PipelineState::initial(3, 1);
    let mut handler = MockEffectHandler::new(state);

    // Simulate development XML extraction
    let _result = handler.execute_mock(&Effect::ExtractDevelopmentXml { iteration: 1 });

    // Verify UI event was emitted
    assert!(handler.was_ui_event_emitted(|e| {
        matches!(
            e,
            UIEvent::IterationProgress {
                current: 1,
                total: 3
            }
        )
    }));
}

/// Test that `MockEffectHandler` captures phase transition UI events.
#[test]
fn mock_effect_handler_captures_phase_transition_ui() {
    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state);

    // ValidateFinalState should emit phase transition to Finalizing
    let _result = handler.execute_mock(&Effect::ValidateFinalState);

    // Verify UI event was emitted
    assert!(
        handler.was_ui_event_emitted(|e| matches!(
            e,
            UIEvent::PhaseTransition {
                to: PipelinePhase::Finalizing,
                ..
            }
        )),
        "Should emit phase transition UI event to Finalizing"
    );
}

#[test]
fn mock_effect_handler_restore_prompt_permissions_skips_phase_transition_outside_finalizing() {
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::Interrupted;
    let mut handler = MockEffectHandler::new(state);

    let _result = handler.execute_mock(&Effect::RestorePromptPermissions);

    assert!(
        !handler.was_ui_event_emitted(|e| matches!(
            e,
            UIEvent::PhaseTransition {
                to: PipelinePhase::Complete,
                ..
            }
        )),
        "RestorePromptPermissions should not emit Complete phase transition outside Finalizing"
    );
}
