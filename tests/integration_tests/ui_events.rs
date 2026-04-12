//! Integration tests for `UIEvent` emission during pipeline execution.
//!
//! These tests verify that:
//! - Phase transitions emit appropriate `UIEvents`
//! - Progress events are emitted during iterations
//! - `UIEvents` do not affect reducer state
//! - `UIEvents` are properly formatted for display
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::ui_event::{UIEvent, XmlOutputContext, XmlOutputType};
use ralph_workflow::reducer::PipelineState;

use crate::test_timeout::with_default_timeout;

#[test]
fn test_development_iteration_emits_progress_ui() {
    with_default_timeout(|| {
        let state = PipelineState::initial(3, 1);
        let mut handler = MockEffectHandler::new(state);

        // Simulate development XML extraction
        let _result = handler.execute_mock(&Effect::ExtractDevelopmentXml { iteration: 1 });

        // Verify UI event was emitted
        assert!(
            handler.was_ui_event_emitted(|e| {
                matches!(
                    e,
                    UIEvent::IterationProgress {
                        current: 1,
                        total: 3
                    }
                )
            }),
            "Should emit IterationProgress UI event"
        );
    });
}

#[test]
fn test_review_pass_emits_progress_ui() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 3);
        let mut handler = MockEffectHandler::new(state);

        // Simulate review pass
        let _result = handler.execute_mock(&Effect::PrepareReviewContext { pass: 2 });

        // Verify UI event was emitted
        assert!(
            handler.was_ui_event_emitted(|e| {
                matches!(e, UIEvent::ReviewProgress { pass: 2, total: 3 })
            }),
            "Should emit ReviewProgress UI event"
        );
    });
}

#[test]
fn test_phase_transition_ui_event_format() {
    with_default_timeout(|| {
        let event = UIEvent::PhaseTransition {
            from: Some(PipelinePhase::Planning),
            to: PipelinePhase::Development,
        };

        let display = event.format_for_display();
        assert!(
            display.contains("Development"),
            "Should contain phase name, got: {display}"
        );
    });
}

#[test]
fn test_validate_final_state_emits_phase_transition() {
    with_default_timeout(|| {
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
    });
}

#[test]
fn test_restore_prompt_permissions_emits_phase_transition() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::Finalizing;
        let mut handler = MockEffectHandler::new(state);

        // RestorePromptPermissions should emit phase transition to Complete
        let _result = handler.execute_mock(&Effect::RestorePromptPermissions);

        // Verify UI event was emitted
        assert!(
            handler.was_ui_event_emitted(|e| matches!(
                e,
                UIEvent::PhaseTransition {
                    to: PipelinePhase::Complete,
                    ..
                }
            )),
            "Should emit phase transition UI event to Complete"
        );
    });
}

#[test]
fn test_ui_events_do_not_affect_reducer_state() {
    with_default_timeout(|| {
        // This test verifies that UIEvents are purely display-only
        // and do not cause any state mutations via the reducer
        use ralph_workflow::reducer::reduce;

        let initial_state = PipelineState::initial(1, 0);

        // Create a pipeline event that transitions state
        // Use planning_phase_completed to transition from Planning to Development
        let event = ralph_workflow::reducer::PipelineEvent::planning_phase_completed();

        // Reduce state
        let new_state = reduce(initial_state, event);

        // State should be updated based on the PipelineEvent, not any UIEvent
        // UIEvents exist separately and never go through the reducer
        assert_eq!(
            new_state.phase,
            PipelinePhase::Development,
            "State should be updated by PipelineEvent, not UIEvent"
        );
    });
}

#[test]
fn test_ui_event_serialization_roundtrip() {
    with_default_timeout(|| {
        let event = UIEvent::IterationProgress {
            current: 5,
            total: 10,
        };

        let json = serde_json::to_string(&event).expect("Should serialize");
        let deserialized: UIEvent = serde_json::from_str(&json).expect("Should deserialize");

        assert_eq!(event, deserialized);
    });
}

#[test]
fn test_all_phase_emojis_are_defined() {
    with_default_timeout(|| {
        // Verify all phases have emojis
        let phases = [
            PipelinePhase::Planning,
            PipelinePhase::Development,
            PipelinePhase::Review,
            PipelinePhase::CommitMessage,
            PipelinePhase::FinalValidation,
            PipelinePhase::Finalizing,
            PipelinePhase::Complete,
            PipelinePhase::Interrupted,
        ];

        for phase in phases {
            let emoji = UIEvent::phase_emoji(&phase);
            assert!(!emoji.is_empty(), "Phase {phase:?} should have an emoji");
        }
    });
}

#[test]
fn test_agent_activity_ui_event() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 0);
        let mut handler = MockEffectHandler::new(state);

        // Simulate agent invocation
        let _result = handler.execute_mock(&Effect::AgentInvocation {
            role: ralph_workflow::agents::AgentRole::Developer,
            agent: "claude".to_string(),
            model: Some("claude-3".to_string()),
            prompt: "Test prompt".to_string(),
        });

        // Verify UI event was emitted
        assert!(
            handler.was_ui_event_emitted(
                |e| matches!(e, UIEvent::AgentActivity { agent, .. } if agent == "claude")
            ),
            "Should emit AgentActivity UI event"
        );
    });
}

#[test]
fn test_apply_planning_outcome_emits_phase_transition_on_success() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 0);
        let mut handler = MockEffectHandler::new(state);

        // Simulate planning completion
        let _result = handler.execute_mock(&Effect::ApplyPlanningOutcome {
            iteration: 1,
            valid: true,
        });

        // Verify phase transition UI event was emitted
        assert!(
            handler.was_ui_event_emitted(|e| matches!(
                e,
                UIEvent::PhaseTransition {
                    to: PipelinePhase::Development,
                    ..
                }
            )),
            "Should emit phase transition UI event to Development"
        );
    });
}

#[test]
fn test_captured_ui_events_cleared_on_clear() {
    with_default_timeout(|| {
        let state = PipelineState::initial(3, 1);
        let mut handler = MockEffectHandler::new(state);

        // Emit some UI events
        let _result = handler.execute_mock(&Effect::ExtractDevelopmentXml { iteration: 1 });

        // Verify UI events were captured
        assert!(
            handler.ui_event_count() > 0,
            "Should have captured UI events"
        );

        // Clear captured events
        handler.clear_captured();

        // Verify all events are cleared
        assert_eq!(handler.ui_event_count(), 0, "UI events should be cleared");
        assert_eq!(handler.effect_count(), 0, "Effects should be cleared");
    });
}

#[test]
fn test_pipeline_start_emits_planning_phase_transition() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 0);
        let mut handler = MockEffectHandler::new(state);

        // CleanupContext is the first effect in Planning phase
        let _result = handler.execute_mock(&Effect::CleanupContext);

        // Should NOT emit phase transition for cleanup
        assert!(
            !handler.was_ui_event_emitted(|e| matches!(e, UIEvent::PhaseTransition { .. })),
            "CleanupContext should not emit phase transition"
        );

        // Clear and test InitializeAgentChain
        handler.clear_captured();
        let _result = handler.execute_mock(&Effect::InitializeAgentChain {
            drain: ralph_workflow::agents::AgentDrain::Planning,
        });

        // InitializeAgentChain in Planning phase should emit Planning transition
        assert!(
            handler.was_ui_event_emitted(|e| matches!(
                e,
                UIEvent::PhaseTransition {
                    from: None,
                    to: PipelinePhase::Planning,
                }
            )),
            "InitializeAgentChain should emit Planning phase transition"
        );
    });
}

#[test]
fn test_review_phase_start_emits_phase_transition() {
    with_default_timeout(|| {
        // Create state already in Review phase (simulates after Development completes)
        // Using (0, 1) sets phase to Review since developer_iters is 0
        let state = PipelineState::initial(0, 1);
        assert_eq!(state.phase, PipelinePhase::Review);

        let mut handler = MockEffectHandler::new(state);

        // InitializeAgentChain for Reviewer should emit Review phase transition
        let _result = handler.execute_mock(&Effect::InitializeAgentChain {
            drain: ralph_workflow::agents::AgentDrain::Review,
        });

        assert!(
            handler.was_ui_event_emitted(|e| matches!(
                e,
                UIEvent::PhaseTransition {
                    to: PipelinePhase::Review,
                    ..
                }
            )),
            "InitializeAgentChain for Reviewer should emit Review phase transition"
        );
    });
}

#[test]
fn test_complete_phase_transition_sequence() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 1);
        let mut handler = MockEffectHandler::new(state);
        let mut all_ui_events = Vec::new();

        // 1. Planning phase (via InitializeAgentChain)
        let result = handler.execute_mock(&Effect::InitializeAgentChain {
            drain: ralph_workflow::agents::AgentDrain::Planning,
        });
        all_ui_events.extend(result.ui_events);

        // 2. Development phase (via ApplyPlanningOutcome success)
        let result = handler.execute_mock(&Effect::ApplyPlanningOutcome {
            iteration: 1,
            valid: true,
        });
        all_ui_events.extend(result.ui_events);

        // Update handler state to Review phase
        handler.state.phase = PipelinePhase::Review;

        // 3. Review phase (via InitializeAgentChain for Reviewer)
        let result = handler.execute_mock(&Effect::InitializeAgentChain {
            drain: ralph_workflow::agents::AgentDrain::Review,
        });
        all_ui_events.extend(result.ui_events);

        // Update state for final validation
        handler.state.phase = PipelinePhase::FinalValidation;

        // 4. Finalizing phase
        let result = handler.execute_mock(&Effect::ValidateFinalState);
        all_ui_events.extend(result.ui_events);

        // Update state for finalizing
        handler.state.phase = PipelinePhase::Finalizing;

        // 5. Complete phase
        let result = handler.execute_mock(&Effect::RestorePromptPermissions);
        all_ui_events.extend(result.ui_events);

        // Verify all expected phase transitions
        let phase_transitions: Vec<_> = all_ui_events
            .iter()
            .filter_map(|e| match e {
                UIEvent::PhaseTransition { to, .. } => Some(*to),
                _ => None,
            })
            .collect();

        assert!(
            phase_transitions.contains(&PipelinePhase::Planning),
            "Should emit Planning transition, got: {phase_transitions:?}"
        );
        assert!(
            phase_transitions.contains(&PipelinePhase::Development),
            "Should emit Development transition, got: {phase_transitions:?}"
        );
        assert!(
            phase_transitions.contains(&PipelinePhase::Review),
            "Should emit Review transition, got: {phase_transitions:?}"
        );
        assert!(
            phase_transitions.contains(&PipelinePhase::Finalizing),
            "Should emit Finalizing transition, got: {phase_transitions:?}"
        );
        assert!(
            phase_transitions.contains(&PipelinePhase::Complete),
            "Should emit Complete transition, got: {phase_transitions:?}"
        );
    });
}

// =========================================================================
// XmlOutput Event Tests
// =========================================================================

#[test]
fn test_validate_planning_xml_emits_xml_output() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 0);
        let mut handler = MockEffectHandler::new(state);

        // Validate plan XML
        let _result = handler.execute_mock(&Effect::ValidatePlanningXml { iteration: 1 });

        // Verify XmlOutput event was emitted with DevelopmentPlan type
        assert!(
            handler.was_ui_event_emitted(|e| matches!(
                e,
                UIEvent::XmlOutput {
                    xml_type: XmlOutputType::DevelopmentPlan,
                    ..
                }
            )),
            "ValidatePlanningXml should emit XmlOutput event with DevelopmentPlan type"
        );
    });
}

#[test]
fn test_development_iteration_emits_xml_output() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 0);
        let mut handler = MockEffectHandler::new(state);

        // Extract development XML
        let _result = handler.execute_mock(&Effect::ExtractDevelopmentXml { iteration: 1 });

        // Verify XmlOutput event was emitted with DevelopmentResult type
        assert!(
            handler.was_ui_event_emitted(|e| matches!(
                e,
                UIEvent::XmlOutput {
                    xml_type: XmlOutputType::DevelopmentResult,
                    ..
                }
            )),
            "ExtractDevelopmentXml should emit XmlOutput event with DevelopmentResult type"
        );
    });
}

#[test]
fn test_review_pass_emits_xml_output() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 1);
        let mut handler = MockEffectHandler::new(state);

        // Validate review issues XML (single-task)
        let _result = handler.execute_mock(&Effect::ValidateReviewIssuesXml { pass: 1 });

        // Verify XmlOutput event was emitted with ReviewIssues type
        assert!(
            handler.was_ui_event_emitted(|e| matches!(
                e,
                UIEvent::XmlOutput {
                    xml_type: XmlOutputType::ReviewIssues,
                    ..
                }
            )),
            "ValidateReviewIssuesXml should emit XmlOutput event with ReviewIssues type"
        );
    });
}

#[test]
fn test_fix_attempt_emits_xml_output() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 1);
        let mut handler = MockEffectHandler::new(state);

        // Validate fix result XML (single-task)
        let _result = handler.execute_mock(&Effect::ValidateFixResultXml { pass: 1 });

        // Verify XmlOutput event was emitted with FixResult type
        assert!(
            handler.was_ui_event_emitted(|e| matches!(
                e,
                UIEvent::XmlOutput {
                    xml_type: XmlOutputType::FixResult,
                    ..
                }
            )),
            "ValidateFixResultXml should emit XmlOutput event with FixResult type"
        );
    });
}

#[test]
fn test_commit_message_emits_xml_output() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 0);
        let mut handler = MockEffectHandler::new(state);

        // Validate commit XML
        let _result = handler.execute_mock(&Effect::ValidateCommitXml);

        // Verify XmlOutput event was emitted with CommitMessage type
        assert!(
            handler.was_ui_event_emitted(|e| matches!(
                e,
                UIEvent::XmlOutput {
                    xml_type: XmlOutputType::CommitMessage,
                    ..
                }
            )),
            "ValidateCommitXml should emit XmlOutput event with CommitMessage type"
        );
    });
}

#[test]
fn test_xml_output_context_includes_iteration() {
    with_default_timeout(|| {
        let state = PipelineState::initial(3, 0);
        let mut handler = MockEffectHandler::new(state);

        // Validate plan XML for iteration 2
        let result = handler.execute_mock(&Effect::ValidatePlanningXml { iteration: 2 });

        // Find the XmlOutput event and check context
        let xml_output = result
            .ui_events
            .iter()
            .find(|e| matches!(e, UIEvent::XmlOutput { .. }));

        assert!(xml_output.is_some(), "Should have XmlOutput event");
        if let Some(UIEvent::XmlOutput { context, .. }) = xml_output {
            assert!(context.is_some(), "Context should be present");
            if let Some(ctx) = context {
                assert_eq!(
                    ctx.iteration,
                    Some(2),
                    "Context should include iteration number"
                );
            }
        }
    });
}

#[test]
fn test_xml_output_context_includes_pass() {
    with_default_timeout(|| {
        let state = PipelineState::initial(1, 3);
        let mut handler = MockEffectHandler::new(state);

        // Validate review issues XML for pass 2 (emits XmlOutput)
        let result = handler.execute_mock(&Effect::ValidateReviewIssuesXml { pass: 2 });

        // Find the XmlOutput event and check context
        let xml_output = result
            .ui_events
            .iter()
            .find(|e| matches!(e, UIEvent::XmlOutput { .. }));

        assert!(xml_output.is_some(), "Should have XmlOutput event");
        if let Some(UIEvent::XmlOutput { context, .. }) = xml_output {
            assert!(context.is_some(), "Context should be present");
            if let Some(ctx) = context {
                assert_eq!(ctx.pass, Some(2), "Context should include pass number");
            }
        }
    });
}

#[test]
fn test_xml_output_format_for_display_renders_semantically() {
    with_default_timeout(|| {
        let event = UIEvent::XmlOutput {
            xml_type: XmlOutputType::DevelopmentResult,
            content: r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Test complete</ralph-summary>
</ralph-development-result>"
                .to_string(),
            context: Some(XmlOutputContext {
                iteration: Some(1),
                pass: None,
                snippets: Vec::new(),
            }),
        };

        let output = event.format_for_display();

        // Verify semantic rendering, not raw XML
        assert!(
            !output.contains("<ralph-"),
            "Should not contain raw XML tags in output: {output}"
        );
        assert!(
            output.contains("✅") || output.contains("completed"),
            "Should have status indicator: {output}"
        );
        assert!(
            output.contains("Test complete"),
            "Should have summary: {output}"
        );
    });
}

#[test]
fn test_xml_output_serialization_roundtrip() {
    with_default_timeout(|| {
        let event = UIEvent::XmlOutput {
            xml_type: XmlOutputType::ReviewIssues,
            content: "<ralph-issues><ralph-issue>Test issue</ralph-issue></ralph-issues>"
                .to_string(),
            context: Some(XmlOutputContext {
                iteration: None,
                pass: Some(1),
                snippets: Vec::new(),
            }),
        };

        let json = serde_json::to_string(&event).expect("Should serialize");
        let deserialized: UIEvent = serde_json::from_str(&json).expect("Should deserialize");

        assert_eq!(event, deserialized, "Roundtrip should preserve event");
    });
}

#[test]
fn test_xml_output_type_all_variants() {
    with_default_timeout(|| {
        // Verify all XmlOutputType variants are distinct
        let types = [
            XmlOutputType::DevelopmentResult,
            XmlOutputType::DevelopmentPlan,
            XmlOutputType::ReviewIssues,
            XmlOutputType::FixResult,
            XmlOutputType::CommitMessage,
        ];

        for (i, t1) in types.iter().enumerate() {
            for (j, t2) in types.iter().enumerate() {
                if i == j {
                    assert_eq!(t1, t2);
                } else {
                    assert_ne!(t1, t2, "{t1:?} should be different from {t2:?}");
                }
            }
        }
    });
}

// =========================================================================
// Single Writer Enforcement Tests
// =========================================================================

/// Tests the single-writer principle: XML output is rendered semantically via
/// `UIEvent::XmlOutput` and the centralized rendering module only.
///
/// This verifies that:
/// 1. `render_ui_event()` produces semantic output (user-friendly status, not raw XML)
/// 2. The rendering module is the single entrypoint for `UIEvent` formatting
/// 3. Phase code does not produce competing raw XML output
#[test]
fn test_single_writer_xml_output_via_ui_event_only() {
    with_default_timeout(|| {
        use ralph_workflow::rendering::render_ui_event;

        let xml_content = r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>Test summary for single-writer verification</ralph-summary>
</ralph-development-result>";

        let event = UIEvent::XmlOutput {
            xml_type: XmlOutputType::DevelopmentResult,
            content: xml_content.to_string(),
            context: Some(XmlOutputContext {
                iteration: Some(1),
                pass: None,
                snippets: Vec::new(),
            }),
        };

        let rendered = render_ui_event(&event);

        // The single writer renders semantically, not as raw XML
        assert!(
            !rendered.contains("<ralph-development-result>"),
            "Single writer should produce semantic output, not raw XML. Got: {rendered}"
        );
        assert!(
            !rendered.contains("<ralph-status>"),
            "Single writer should not emit raw XML status tags. Got: {rendered}"
        );
        assert!(
            rendered.contains("✅") || rendered.to_lowercase().contains("completed"),
            "Single writer should produce user-friendly status. Got: {rendered}"
        );
        assert!(
            rendered.contains("Test summary for single-writer verification"),
            "Single writer should include content from XML. Got: {rendered}"
        );

        // Verify UIEvent::format_for_display() delegates to render_ui_event()
        // (same output confirms single entrypoint)
        let format_display_output = event.format_for_display();
        assert_eq!(
            rendered, format_display_output,
            "format_for_display() must delegate to render_ui_event()"
        );
    });
}

/// Tests that the centralized renderer routes all `XmlOutputType` variants
/// through the single entrypoint (`render_ui_event`). Well-formed XML produces
/// semantic output; malformed XML gracefully falls back to raw display with
/// a warning, but still through the single writer.
#[test]
fn test_single_writer_handles_all_xml_output_types() {
    with_default_timeout(|| {
        use ralph_workflow::rendering::render_ui_event;

        // Well-formed XML: semantic rendering produces user-friendly output
        let wellformed_cases = [
            (
                XmlOutputType::DevelopmentResult,
                r"<ralph-development-result>
<ralph-status>completed</ralph-status>
<ralph-summary>done</ralph-summary>
</ralph-development-result>",
                "✅", // expected indicator in output
            ),
            (
                XmlOutputType::ReviewIssues,
                // Note: <ralph-no-issues-found> must be nested inside <ralph-issues>
                r"<ralph-issues>
<ralph-no-issues-found>All code is approved</ralph-no-issues-found>
</ralph-issues>",
                "✅", // approval checkmark
            ),
            (
                XmlOutputType::CommitMessage,
                r"<ralph-commit>
<ralph-subject>test: add feature</ralph-subject>
<ralph-body>Body text</ralph-body>
</ralph-commit>",
                "test: add feature", // subject should appear
            ),
        ];

        for (xml_type, content, expected_indicator) in wellformed_cases {
            let event = UIEvent::XmlOutput {
                xml_type: xml_type.clone(),
                content: content.to_string(),
                context: Some(XmlOutputContext::default()),
            };

            let via_render = render_ui_event(&event);
            let via_format = event.format_for_display();

            // Both paths must produce identical output (single writer)
            assert_eq!(
                via_render, via_format,
                "format_for_display must delegate to render_ui_event for {xml_type:?}"
            );

            // Well-formed XML produces semantic output with expected indicator
            assert!(
                via_render.contains(expected_indicator),
                "Single writer should produce semantic output with '{expected_indicator}' for {xml_type:?}. Got: {via_render}"
            );
        }

        // Malformed XML: graceful fallback still goes through single writer
        let malformed_event = UIEvent::XmlOutput {
            xml_type: XmlOutputType::DevelopmentResult,
            content: "<incomplete>".to_string(),
            context: None,
        };

        let fallback_render = render_ui_event(&malformed_event);
        let fallback_format = malformed_event.format_for_display();

        // Even fallback must go through single writer
        assert_eq!(
            fallback_render, fallback_format,
            "Malformed XML fallback must also go through single writer"
        );
        // Fallback shows warning indicator
        assert!(
            fallback_render.contains("⚠️") || fallback_render.contains("Unable to parse"),
            "Fallback should indicate parsing issue. Got: {fallback_render}"
        );
    });
}
