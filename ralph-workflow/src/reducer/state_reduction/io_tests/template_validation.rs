//! Reducer unit tests for template rendering substitution log validation.
//!
//! These tests verify that the reducer correctly handles `TemplateRendered` events
//! and stores substitution logs in state for validation and observability.
//!
//! Per the reducer architecture, validation is reducer-owned and derived from
//! the substitution log when the `TemplateRendered` event is reduced.

use crate::prompts::{SubstitutionEntry, SubstitutionLog, SubstitutionSource};
use crate::reducer::event::{PipelineEvent, PipelinePhase, PromptInputEvent};
use crate::reducer::state::PipelineState;
use crate::reducer::state_reduction::reduce;

#[test]
fn test_reduce_template_rendered_complete_with_defaults() {
    // Test that reducer stores complete substitution log with defaults in state
    let state = PipelineState::initial(1, 0);

    let log = SubstitutionLog {
        template_name: "commit_message_xml".to_string(),
        substituted: vec![
            SubstitutionEntry {
                name: "DIFF".to_string(),
                source: SubstitutionSource::Value,
            },
            SubstitutionEntry {
                name: "OPTIONAL".to_string(),
                source: SubstitutionSource::Default,
            },
        ],
        unsubstituted: vec![],
    };

    let event = PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
        phase: PipelinePhase::CommitMessage,
        template_name: "commit_message_xml".to_string(),
        log,
    });

    let new_state = reduce(state, event);

    // Verify the log is stored in state
    assert!(
        new_state.last_substitution_log.is_some(),
        "Substitution log should be stored in state"
    );
    assert!(
        !new_state.template_validation_failed,
        "Complete substitution log should not mark validation as failed"
    );
    assert!(
        new_state.template_validation_unsubstituted.is_empty(),
        "No unsubstituted placeholders should be recorded for complete logs"
    );

    let stored_log = new_state.last_substitution_log.unwrap();

    // Verify completeness
    assert!(
        stored_log.is_complete(),
        "Stored log should be complete (no unsubstituted placeholders)"
    );

    // Verify substituted entries
    assert_eq!(
        stored_log.substituted.len(),
        2,
        "Should have 2 substituted entries"
    );
    assert_eq!(stored_log.substituted[0].name, "DIFF");
    assert_eq!(stored_log.substituted[0].source, SubstitutionSource::Value);
    assert_eq!(stored_log.substituted[1].name, "OPTIONAL");
    assert_eq!(
        stored_log.substituted[1].source,
        SubstitutionSource::Default
    );

    // Verify no unsubstituted
    assert!(
        stored_log.unsubstituted.is_empty(),
        "Should have no unsubstituted placeholders"
    );

    // Verify template name
    assert_eq!(stored_log.template_name, "commit_message_xml");
}

#[test]
fn test_reduce_template_rendered_incomplete() {
    // Test that reducer stores incomplete substitution log in state
    let state = PipelineState::initial(1, 0);

    let log = SubstitutionLog {
        template_name: "commit_message_xml".to_string(),
        substituted: vec![SubstitutionEntry {
            name: "A".to_string(),
            source: SubstitutionSource::Value,
        }],
        unsubstituted: vec!["B".to_string(), "C".to_string()],
    };

    let event = PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
        phase: PipelinePhase::CommitMessage,
        template_name: "commit_message_xml".to_string(),
        log,
    });

    let new_state = reduce(state, event);

    // Verify the log is stored in state
    assert!(
        new_state.last_substitution_log.is_some(),
        "Substitution log should be stored in state even if incomplete"
    );
    assert!(
        new_state.template_validation_failed,
        "Incomplete substitution log should mark validation as failed"
    );
    assert_eq!(
        new_state.template_validation_unsubstituted,
        vec!["B".to_string(), "C".to_string()],
        "Unsubstituted placeholders should be recorded for validation failures"
    );

    let stored_log = new_state.last_substitution_log.unwrap();

    // Verify incompleteness
    assert!(
        !stored_log.is_complete(),
        "Stored log should be incomplete (has unsubstituted placeholders)"
    );

    // Verify unsubstituted list
    assert_eq!(
        stored_log.unsubstituted.len(),
        2,
        "Should have 2 unsubstituted placeholders"
    );
    assert_eq!(stored_log.unsubstituted[0], "B");
    assert_eq!(stored_log.unsubstituted[1], "C");
}

#[test]
fn test_reduce_template_rendered_empty_with_default() {
    // Test that EmptyWithDefault source is handled correctly
    let state = PipelineState::initial(1, 0);

    let log = SubstitutionLog {
        template_name: "test_template".to_string(),
        substituted: vec![
            SubstitutionEntry {
                name: "NAME".to_string(),
                source: SubstitutionSource::EmptyWithDefault,
            },
            SubstitutionEntry {
                name: "TITLE".to_string(),
                source: SubstitutionSource::Value,
            },
        ],
        unsubstituted: vec![],
    };

    let event = PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
        phase: PipelinePhase::CommitMessage,
        template_name: "commit_message_xml".to_string(),
        log,
    });

    let new_state = reduce(state, event);

    let stored_log = new_state
        .last_substitution_log
        .expect("Log should be stored");

    // Verify completeness (EmptyWithDefault counts as complete)
    assert!(
        stored_log.is_complete(),
        "Log with EmptyWithDefault should be complete"
    );

    // Verify defaults_used helper includes EmptyWithDefault
    let defaults = stored_log.defaults_used();
    assert_eq!(defaults.len(), 1, "Should have 1 default used");
    assert!(
        defaults.contains(&"NAME"),
        "NAME should be in defaults_used (EmptyWithDefault)"
    );
}

#[test]
fn test_reduce_template_rendered_log_persists_across_state() {
    // Test that the substitution log persists in state and can be accessed
    // across multiple state transitions
    let state = PipelineState::initial(1, 0);

    let log = SubstitutionLog {
        template_name: "first_template".to_string(),
        substituted: vec![SubstitutionEntry {
            name: "VAR1".to_string(),
            source: SubstitutionSource::Value,
        }],
        unsubstituted: vec![],
    };

    let event1 = PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
        phase: PipelinePhase::Planning,
        template_name: "first_template".to_string(),
        log,
    });

    let state_after_first = reduce(state, event1);

    // Verify first log is stored
    assert!(state_after_first.last_substitution_log.is_some());
    assert_eq!(
        state_after_first
            .last_substitution_log
            .as_ref()
            .unwrap()
            .template_name,
        "first_template"
    );

    // Emit a second TemplateRendered event
    let log2 = SubstitutionLog {
        template_name: "second_template".to_string(),
        substituted: vec![SubstitutionEntry {
            name: "VAR2".to_string(),
            source: SubstitutionSource::Default,
        }],
        unsubstituted: vec![],
    };

    let event2 = PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
        phase: PipelinePhase::Development,
        template_name: "second_template".to_string(),
        log: log2,
    });

    let state_after_second = reduce(state_after_first, event2);

    // Verify second log replaces the first (most recent log is stored)
    assert!(state_after_second.last_substitution_log.is_some());
    assert_eq!(
        state_after_second
            .last_substitution_log
            .as_ref()
            .unwrap()
            .template_name,
        "second_template"
    );
}

#[test]
fn test_reduce_template_rendered_different_phases() {
    // Test that TemplateRendered works across different pipeline phases
    let phases_to_test = vec![
        PipelinePhase::Planning,
        PipelinePhase::Development,
        PipelinePhase::Review,
        PipelinePhase::CommitMessage,
    ];

    for phase in phases_to_test {
        let state = PipelineState::initial(1, 0);

        let log = SubstitutionLog {
            template_name: format!("{phase:?}_template"),
            substituted: vec![SubstitutionEntry {
                name: "TEST_VAR".to_string(),
                source: SubstitutionSource::Value,
            }],
            unsubstituted: vec![],
        };

        let event = PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
            phase,
            template_name: format!("{phase:?}_template"),
            log: log.clone(),
        });

        let new_state = reduce(state, event);

        // Verify log is stored regardless of phase
        assert!(
            new_state.last_substitution_log.is_some(),
            "Log should be stored for phase {phase:?}"
        );
        assert_eq!(
            new_state.last_substitution_log.unwrap().template_name,
            format!("{phase:?}_template")
        );
    }
}

#[test]
fn test_reduce_template_rendered_with_multiple_defaults() {
    // Test that multiple default substitutions are tracked correctly
    let state = PipelineState::initial(1, 0);

    let log = SubstitutionLog {
        template_name: "multi_default_template".to_string(),
        substituted: vec![
            SubstitutionEntry {
                name: "A".to_string(),
                source: SubstitutionSource::Value,
            },
            SubstitutionEntry {
                name: "B".to_string(),
                source: SubstitutionSource::Default,
            },
            SubstitutionEntry {
                name: "C".to_string(),
                source: SubstitutionSource::EmptyWithDefault,
            },
            SubstitutionEntry {
                name: "D".to_string(),
                source: SubstitutionSource::Default,
            },
        ],
        unsubstituted: vec![],
    };

    let event = PipelineEvent::PromptInput(PromptInputEvent::TemplateRendered {
        phase: PipelinePhase::Development,
        template_name: "multi_default_template".to_string(),
        log,
    });

    let new_state = reduce(state, event);

    let stored_log = new_state
        .last_substitution_log
        .expect("Log should be stored");

    // Verify all entries are tracked
    assert_eq!(stored_log.substituted.len(), 4);

    // Verify defaults_used includes B, C, D
    let defaults = stored_log.defaults_used();
    assert_eq!(defaults.len(), 3, "Should have 3 defaults used");
    assert!(defaults.contains(&"B"));
    assert!(defaults.contains(&"C"));
    assert!(defaults.contains(&"D"));
    assert!(!defaults.contains(&"A"));
}
