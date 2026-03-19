use super::common::TestFixture;
use crate::reducer::effect::{Effect, EffectHandler};
use crate::reducer::event::PipelinePhase;
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::PipelineState;
use crate::reducer::ui_event::UIEvent;

#[test]
fn restore_prompt_permissions_emits_complete_transition_in_finalizing() {
    let mut fixture = TestFixture::new();

    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::Finalizing;
    let mut handler = MainEffectHandler::new(state);

    let mut ctx = fixture.ctx();
    let result = handler.execute(Effect::RestorePromptPermissions, &mut ctx);

    assert!(result.is_ok(), "RestorePromptPermissions should succeed");

    let result = result.unwrap();
    assert!(
        result.ui_events.iter().any(|event| matches!(
            event,
            UIEvent::PhaseTransition {
                to: PipelinePhase::Complete,
                ..
            }
        )),
        "Expected phase transition UI event to Complete"
    );
}
