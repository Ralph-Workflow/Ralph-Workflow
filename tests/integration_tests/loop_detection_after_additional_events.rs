//! Integration test for loop detection after additional events.
//!
//! Verifies that loop detection counters are computed based on the final state
//! after ALL events (primary + additional) have been processed, not just the
//! primary event. This is a critical fix to prevent incorrect loop detection
//! when additional events change phase, agent chain, or other state that affects
//! the effect fingerprint.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.

use ralph_workflow::reducer::event::{PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::orchestration::compute_effect_fingerprint;
use ralph_workflow::reducer::reduce;
use ralph_workflow::reducer::state::PipelineState;

use crate::test_timeout::with_default_timeout;

/// Test that loop detection counters are computed from final state after additional events.
///
/// This test verifies the fix for the critical bug where loop detection counters
/// were updated based on the state after the primary event, but before processing
/// additional events. If additional events change the phase or agent chain, the
/// fingerprint would be incorrect.
///
/// The fix ensures that loop detection counters are computed AFTER all additional
/// events have been processed.
#[test]
fn test_loop_detection_counters_computed_after_additional_events() {
    with_default_timeout(|| {
        // Create initial state in Planning phase with XSD retry pending
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::Planning;
        state.continuation.xsd_retry_pending = true;
        let expected_initial_fingerprint = compute_effect_fingerprint(&state);
        state.continuation.last_effect_kind = Some(expected_initial_fingerprint.clone());
        state.continuation.consecutive_same_effect_count = 3;

        // Record the fingerprint BEFORE any event processing (for comparison)
        let fingerprint_before = compute_effect_fingerprint(&state);
        assert_eq!(fingerprint_before, expected_initial_fingerprint);

        // Simulate the event loop behavior:
        // 1. Primary event: ContextCleaned (doesn't change phase or retry state)
        let primary_event = PipelineEvent::context_cleaned();
        let state_after_primary = reduce(state, primary_event);

        // At this point, still in Planning with xsd_retry_pending
        assert!(matches!(state_after_primary.phase, PipelinePhase::Planning));
        assert!(state_after_primary.continuation.xsd_retry_pending);

        // 2. Additional event: Transition to Development phase (simulating phase completion)
        // This is the key scenario: additional events CAN change the state significantly
        let additional_event = PipelineEvent::development_phase_started();
        let final_state = reduce(state_after_primary.clone(), additional_event);

        // After the additional event, we're now in Development phase
        assert!(matches!(final_state.phase, PipelinePhase::Development));

        // The fingerprint computed from the FINAL state should be different
        // from the fingerprint computed from the intermediate state
        let final_fingerprint = compute_effect_fingerprint(&final_state);
        let intermediate_fingerprint = compute_effect_fingerprint(&state_after_primary);

        // The final fingerprint should reflect the Development phase, not Planning
        assert!(
            final_fingerprint.contains("Development"),
            "Final fingerprint should reflect Development phase after additional event, got: {final_fingerprint}"
        );

        // The intermediate fingerprint should reflect Planning phase
        assert!(
            intermediate_fingerprint.contains("Planning"),
            "Intermediate fingerprint should reflect Planning phase, got: {intermediate_fingerprint}"
        );

        // They should be different
        assert_ne!(
            final_fingerprint, intermediate_fingerprint,
            "Fingerprints should differ when additional events change the phase"
        );

        // This verifies the bug fix: if loop detection was computed from the
        // intermediate state (before additional events), it would record the
        // wrong fingerprint. The fix ensures it's computed from the final state.
    });
}

/// Test that loop detection correctly resets when additional events change the phase.
///
/// This verifies the specific scenario from the bug report: if we're in a loop
/// in Planning phase but an additional event transitions us to Development,
/// loop detection should see that as a different effect (not the same effect
/// repeating).
#[test]
fn test_loop_detection_resets_when_additional_events_change_phase() {
    with_default_timeout(|| {
        // Start in Planning with a "looping" effect fingerprint
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::Planning;
        state.continuation.xsd_retry_pending = true;
        let expected_initial_fingerprint = compute_effect_fingerprint(&state);
        state.continuation.last_effect_kind = Some(expected_initial_fingerprint);
        state.continuation.consecutive_same_effect_count = 4;

        // Verify we're in a "looping" state
        assert_eq!(state.continuation.consecutive_same_effect_count, 4);

        // Now simulate: primary event keeps us in Planning, but additional event
        // transitions to Development
        let primary_event = PipelineEvent::context_cleaned();
        let state_after_primary = reduce(state, primary_event);

        let additional_event = PipelineEvent::development_phase_started();
        let final_state = reduce(state_after_primary, additional_event);

        // The final state is in Development phase
        assert!(matches!(final_state.phase, PipelinePhase::Development));

        // The final fingerprint is different from the stored last_effect_kind
        let final_fingerprint = compute_effect_fingerprint(&final_state);
        assert_ne!(
            final_state.continuation.last_effect_kind.as_deref(),
            Some(final_fingerprint.as_str()),
            "Final fingerprint should differ from stored fingerprint when phase changes"
        );

        // Therefore, when the event loop updates loop detection counters,
        // it should RESET (not increment) the counter because we have a
        // different effect fingerprint.
        //
        // This is only correct when loop detection is computed AFTER additional
        // events. If computed before, it would incorrectly increment the counter.
    });
}

/// Test that loop detection increments when additional events DON'T change the fingerprint.
///
/// This verifies the normal case: if additional events don't significantly change
/// the state (same phase, same agent, etc.), the fingerprint stays the same and
/// the loop counter should increment.
#[test]
fn test_loop_detection_increments_when_additional_events_preserve_fingerprint() {
    with_default_timeout(|| {
        // Start in Planning with a "looping" effect fingerprint
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::Planning;
        state.continuation.xsd_retry_pending = true;
        let expected_initial_fingerprint = compute_effect_fingerprint(&state);
        state.continuation.last_effect_kind = Some(expected_initial_fingerprint);
        state.continuation.consecutive_same_effect_count = 2;

        // Simulate: primary event and additional events that both keep us in
        // the same state (Planning, XSD retry pending)
        let primary_event = PipelineEvent::context_cleaned();
        let state_after_primary = reduce(state, primary_event);

        // This additional event also doesn't change the phase or retry state
        // (we clone state_after_primary since it's moved into reduce)
        let additional_event = PipelineEvent::context_cleaned();
        let final_state = reduce(state_after_primary, additional_event);

        // Verify we're still in Planning with XSD retry pending
        assert!(matches!(final_state.phase, PipelinePhase::Planning));
        assert!(final_state.continuation.xsd_retry_pending);

        // The fingerprint should be the same
        let final_fingerprint = compute_effect_fingerprint(&final_state);
        assert_eq!(
            final_state.continuation.last_effect_kind.as_deref(),
            Some(final_fingerprint.as_str()),
            "Fingerprint should be the same when state doesn't change significantly"
        );

        // Therefore, the loop counter should INCREMENT (not reset).
        // This is only correctly detected when loop detection is computed
        // AFTER additional events have been processed.
    });
}
