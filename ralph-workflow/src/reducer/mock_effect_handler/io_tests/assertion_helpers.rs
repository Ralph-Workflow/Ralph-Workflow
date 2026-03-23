// Tests for helper method assertions (clear_captured, was_effect_executed, etc.).

use super::*;

#[test]
fn mock_effect_handler_clear_captured_works() {
    let state = PipelineState::initial(1, 0);
    let handler = MockEffectHandler::new(state);

    // Manually push an effect for testing (simulating execute)
    handler.captured_state.push_effect(Effect::CreateCommit {
        message: "test".to_string(),
        files: vec![],
        excluded_files: vec![],
    });

    assert_eq!(handler.effect_count(), 1);

    handler.clear_captured();

    assert_eq!(handler.effect_count(), 0);
    assert!(handler.captured_effects().is_empty());
}

#[test]
fn mock_effect_handler_was_effect_executed_works() {
    let state = PipelineState::initial(1, 0);
    let handler = MockEffectHandler::new(state);

    // Manually push effects for testing
    handler.captured_state.push_effect(Effect::CreateCommit {
        message: "test commit".to_string(),
        files: vec![],
        excluded_files: vec![],
    });
    handler
        .captured_state
        .push_effect(Effect::PreparePlanningPrompt {
            iteration: 1,
            prompt_mode: crate::reducer::state::PromptMode::Normal,
        });

    assert!(handler.was_effect_executed(|e| matches!(e, Effect::CreateCommit { .. })));
    assert!(handler.was_effect_executed(|e| matches!(e, Effect::PreparePlanningPrompt { .. })));
    assert!(!handler.was_effect_executed(|e| matches!(e, Effect::ValidateFinalState)));
}
