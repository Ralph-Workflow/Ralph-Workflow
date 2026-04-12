use crate::agents::AgentRole;
use crate::reducer::effect::{Effect, EffectResult};
use crate::reducer::event::{PipelineEvent, PipelinePhase};
use crate::reducer::ui_event::UIEvent;

#[test]
fn test_effect_serialization() {
    let effect = Effect::AgentInvocation {
        role: AgentRole::Developer,
        agent: "claude".to_string(),
        model: None,
        prompt: "test".to_string(),
    };

    let json = serde_json::to_string(&effect).unwrap();
    let deserialized: Effect = serde_json::from_str(&json).unwrap();

    match deserialized {
        Effect::AgentInvocation {
            role,
            agent,
            model,
            prompt,
        } => {
            assert_eq!(role, AgentRole::Developer);
            assert_eq!(agent, "claude");
            assert!(model.is_none());
            assert_eq!(prompt, "test");
        }
        _ => panic!("Expected AgentInvocation effect"),
    }
}

#[test]
fn test_effect_result_event_only() {
    // Use ContextCleaned as a representative event that exists
    let event = PipelineEvent::ContextCleaned;
    let result = EffectResult::event(event);

    assert!(matches!(result.event, PipelineEvent::ContextCleaned));
    assert!(result.ui_events.is_empty());
}

#[test]
fn test_effect_result_with_ui() {
    let event = PipelineEvent::development_iteration_completed(1, true);
    let ui_events = vec![UIEvent::IterationProgress {
        current: 1,
        total: 3,
    }];

    let result = EffectResult::with_ui(event, ui_events);

    assert!(matches!(
        result.event,
        PipelineEvent::Development(
            crate::reducer::event::DevelopmentEvent::IterationCompleted { .. }
        )
    ));
    assert_eq!(result.ui_events.len(), 1);
    assert!(matches!(
        result.ui_events[0],
        UIEvent::IterationProgress {
            current: 1,
            total: 3
        }
    ));
}

#[test]
fn test_effect_result_with_ui_event_builder() {
    let event = PipelineEvent::plan_generation_completed(1, true);

    let result = EffectResult::event(event).with_ui_event(UIEvent::PhaseTransition {
        from: Some(PipelinePhase::Planning),
        to: PipelinePhase::Development,
    });

    assert_eq!(result.ui_events.len(), 1);
    assert!(matches!(
        result.ui_events[0],
        UIEvent::PhaseTransition { .. }
    ));
}

#[test]
fn test_effect_result_multiple_ui_events() {
    let event = PipelineEvent::development_iteration_completed(2, true);

    let result = EffectResult::event(event)
        .with_ui_event(UIEvent::IterationProgress {
            current: 2,
            total: 5,
        })
        .with_ui_event(UIEvent::AgentActivity {
            agent: "claude".to_string(),
            message: "Completed iteration".to_string(),
        });

    assert_eq!(result.ui_events.len(), 2);
    assert!(matches!(
        result.ui_events[0],
        UIEvent::IterationProgress {
            current: 2,
            total: 5
        }
    ));
    assert!(matches!(result.ui_events[1], UIEvent::AgentActivity { .. }));
}
