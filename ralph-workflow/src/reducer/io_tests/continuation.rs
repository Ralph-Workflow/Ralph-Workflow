//! Tests for continuation state handling in the reducer.
//!
//! These tests verify that pending flags are correctly cleared to prevent infinite loops.
//! Each test reproduces a specific bug scenario where `determine_next_effect()` would
//! repeatedly return the same effect because the corresponding pending flag was never cleared.

use super::*;
use crate::agents::AgentRole;
use crate::reducer::effect::Effect;
use crate::reducer::orchestration::determine_next_effect;
use crate::reducer::state::{AgentChainState, ContinuationState};

/// Regression test for Development continuation infinite loop bug.
///
/// Bug scenario:
/// 1. State has `continue_pending=true`, `context_write_pending=false`
/// 2. `determine_next_effect()` returns `PrepareDevelopmentContext`
/// 3. Handler emits `ContextPrepared`
/// 4. Reducer does NOT clear `continue_pending`
/// 5. `determine_next_effect()` returns `PrepareDevelopmentContext` again -> infinite loop
///
/// Fix: `DevelopmentEvent::ContextPrepared` must clear `continue_pending`.
#[test]
fn test_context_prepared_clears_continue_pending_to_prevent_infinite_loop() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 1,
        total_iterations: 5,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(crate::agents::AgentDrain::Development),
        continuation: ContinuationState {
            continue_pending: true,
            context_write_pending: false,
            continuation_attempt: 1,
            ..ContinuationState::default()
        },
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..PipelineState::initial(5, 2)
    };

    // Before fix: determine_next_effect returns PrepareDevelopmentContext
    // (because continue_pending is true and context_write_pending is false)
    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::PrepareDevelopmentContext { .. }),
        "Expected PrepareDevelopmentContext when continue_pending=true, got {effect:?}"
    );

    // Apply ContextPrepared event
    let new_state = reduce(state, PipelineEvent::development_context_prepared(1));

    // After fix: continue_pending should be cleared
    assert!(
        !new_state.continuation.continue_pending,
        "continue_pending should be false after ContextPrepared to prevent infinite loop"
    );

    // The next effect should progress to PrepareDevelopmentPrompt, not back to PrepareDevelopmentContext
    let next_effect = determine_next_effect(&new_state);
    assert!(
        matches!(next_effect, Effect::MaterializeDevelopmentInputs { .. }),
        "Expected MaterializeDevelopmentInputs after ContextPrepared, got {next_effect:?}"
    );
}

/// Verify that `ContextPrepared` still sets `development_context_prepared_iteration` correctly.
#[test]
fn test_context_prepared_still_sets_iteration() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 3,
        development_context_prepared_iteration: None,
        ..PipelineState::initial(5, 2)
    };

    let new_state = reduce(state, PipelineEvent::development_context_prepared(3));

    assert_eq!(new_state.development_context_prepared_iteration, Some(3));
}

/// Verify that `ContextPrepared` clears `continue_pending` even when it was not set.
/// This is a defensive check - clearing an already-false flag should be a no-op.
#[test]
fn test_context_prepared_is_idempotent_on_continue_pending() {
    let state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 2,
        continuation: ContinuationState {
            continue_pending: false, // Already false
            ..ContinuationState::default()
        },
        ..PipelineState::initial(5, 2)
    };

    let new_state = reduce(state, PipelineEvent::development_context_prepared(2));

    // Should still be false (no change)
    assert!(!new_state.continuation.continue_pending);
}

// ============================================================================
// Fix Continuation Tests (Review Phase)
// ============================================================================

/// Regression test for Fix continuation infinite loop bug.
///
/// Bug scenario:
/// 1. State has `fix_continue_pending=true`
/// 2. `determine_next_effect()` returns `PrepareFixPrompt` (via `derive_continuation_effect`)
/// 3. Handler emits `FixPromptPrepared`
/// 4. Reducer does NOT clear `fix_continue_pending`
/// 5. `determine_next_effect()` returns `PrepareFixPrompt` again -> infinite loop
///
/// Fix: `ReviewEvent::FixPromptPrepared` must clear `fix_continue_pending`.
#[test]
fn test_fix_prompt_prepared_clears_fix_continue_pending_to_prevent_infinite_loop() {
    let mut state = PipelineState::initial(5, 2);
    state.phase = PipelinePhase::Review;
    state.reviewer_pass = 0;
    state.total_reviewer_passes = 2;
    state.review_issues_found = true;
    state.agent_chain = AgentChainState::initial()
        .with_agents(
            vec!["claude".to_string()],
            vec![vec![]],
            AgentRole::Reviewer,
        )
        .with_drain(crate::agents::AgentDrain::Fix);
    state.continuation = ContinuationState {
        fix_continue_pending: true,
        fix_continuation_attempt: 1,
        ..ContinuationState::default()
    };
    // Simulate mid-pipeline (permissions already locked at startup)
    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;

    // Before fix: determine_next_effect returns PrepareFixPrompt
    // (because fix_continue_pending is true and continuations are not exhausted)
    let effect = determine_next_effect(&state);
    assert!(
        matches!(effect, Effect::PrepareFixPrompt { .. }),
        "Expected PrepareFixPrompt when fix_continue_pending=true, got {effect:?}"
    );

    // Apply FixPromptPrepared event
    let new_state = reduce(state, PipelineEvent::fix_prompt_prepared(0));

    // After fix: fix_continue_pending should be cleared
    assert!(
        !new_state.continuation.fix_continue_pending,
        "fix_continue_pending should be false after FixPromptPrepared to prevent infinite loop"
    );

    // The next effect should progress to CleanupRequiredFiles (for fix_result.xml), not back to PrepareFixPrompt
    let next_effect = determine_next_effect(&new_state);
    assert!(
        matches!(next_effect, Effect::CleanupRequiredFiles { ref files } if files.iter().any(|f| f.contains("fix_result.xml"))),
        "Expected CleanupRequiredFiles for fix_result.xml after FixPromptPrepared, got {next_effect:?}"
    );
}

/// Verify that `FixPromptPrepared` still sets `fix_prompt_prepared_pass` correctly.
#[test]
fn test_fix_prompt_prepared_still_sets_pass() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 1,
        fix_prompt_prepared_pass: None,
        ..PipelineState::initial(5, 2)
    };

    let new_state = reduce(state, PipelineEvent::fix_prompt_prepared(1));

    assert_eq!(new_state.fix_prompt_prepared_pass, Some(1));
}

/// Verify that `FixPromptPrepared` clears `fix_continue_pending` even when it was not set.
#[test]
fn test_fix_prompt_prepared_is_idempotent_on_fix_continue_pending() {
    let state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        continuation: ContinuationState {
            fix_continue_pending: false, // Already false
            ..ContinuationState::default()
        },
        ..PipelineState::initial(5, 2)
    };

    let new_state = reduce(state, PipelineEvent::fix_prompt_prepared(0));

    // Should still be false (no change)
    assert!(!new_state.continuation.fix_continue_pending);
}

// ============================================================================
// Integration-Style Tests (Event Loop Simulation)
// ============================================================================

use crate::reducer::state::DevelopmentStatus;

/// Simulates running the event loop to verify no infinite loops occur.
///
/// This test starts with a state that has `continue_pending=true` (continuation mode)
/// and runs through the Development phase sequencing to verify that the pipeline
/// progresses correctly without getting stuck.
#[test]
fn test_continuation_does_not_cause_infinite_loop_in_event_loop_simulation() {
    let mut state = PipelineState {
        phase: PipelinePhase::Development,
        iteration: 0,
        total_iterations: 1,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Developer,
            )
            .with_drain(crate::agents::AgentDrain::Development),
        continuation: ContinuationState {
            continue_pending: true,
            context_write_pending: false,
            continuation_attempt: 1,
            ..ContinuationState::default()
        },
        development_context_prepared_iteration: None,
        ..PipelineState::initial(1, 0)
    };

    let max_iterations = 100;
    let mut last_effect_discriminant = None;
    let mut repeat_count = 0;

    for i in 0..max_iterations {
        let effect = determine_next_effect(&state);
        let current_discriminant = std::mem::discriminant(&effect);

        // Track consecutive repeats of the same effect type
        if Some(current_discriminant) == last_effect_discriminant {
            repeat_count += 1;
            assert!(repeat_count <= 5,
                "Potential infinite loop detected at iteration {i}: effect {effect:?} repeated {repeat_count} times"
            );
        } else {
            repeat_count = 1;
            last_effect_discriminant = Some(current_discriminant);
        }

        // Simulate applying the effect by reducing the corresponding event
        state = match effect {
            Effect::LockPromptPermissions => {
                reduce(state, PipelineEvent::prompt_permissions_locked(None))
            }
            Effect::RestorePromptPermissions => {
                reduce(state, PipelineEvent::prompt_permissions_restored())
            }
            Effect::PrepareDevelopmentContext { iteration } => reduce(
                state,
                PipelineEvent::development_context_prepared(iteration),
            ),
            Effect::MaterializeDevelopmentInputs { iteration } => {
                let sig = state.agent_chain.consumer_signature_sha256();
                let prompt = crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Prompt,
                    content_id_sha256: "id".to_string(),
                    consumer_signature_sha256: sig.clone(),
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                };
                let plan = crate::reducer::state::MaterializedPromptInput {
                    kind: crate::reducer::state::PromptInputKind::Plan,
                    content_id_sha256: "id".to_string(),
                    consumer_signature_sha256: sig,
                    original_bytes: 1,
                    final_bytes: 1,
                    model_budget_bytes: None,
                    inline_budget_bytes: None,
                    representation: crate::reducer::state::PromptInputRepresentation::Inline,
                    reason: crate::reducer::state::PromptMaterializationReason::WithinBudgets,
                };
                reduce(
                    state,
                    PipelineEvent::development_inputs_materialized(iteration, prompt, plan),
                )
            }
            Effect::PrepareDevelopmentPrompt { iteration, .. } => {
                reduce(state, PipelineEvent::development_prompt_prepared(iteration))
            }
            Effect::CleanupRequiredFiles { files }
                if files.iter().any(|f| f.contains("development_result.xml")) =>
            {
                let iteration = state.iteration;
                reduce(state, PipelineEvent::development_xml_cleaned(iteration))
            }
            Effect::InvokeDevelopmentAgent { iteration } => {
                reduce(state, PipelineEvent::development_agent_invoked(iteration))
            }
            Effect::ExtractDevelopmentXml { iteration } => {
                reduce(state, PipelineEvent::development_xml_extracted(iteration))
            }
            Effect::ValidateDevelopmentXml { iteration } => reduce(
                state,
                PipelineEvent::development_xml_validated(
                    iteration,
                    DevelopmentStatus::Completed,
                    "done".to_string(),
                    None,
                    None,
                ),
            ),
            Effect::ArchiveDevelopmentXml { iteration } => {
                reduce(state, PipelineEvent::development_xml_archived(iteration))
            }
            Effect::ApplyDevelopmentOutcome { iteration } => reduce(
                state,
                PipelineEvent::development_iteration_completed(iteration, true),
            ),
            Effect::SaveCheckpoint { .. } => {
                // Phase complete - success!
                break;
            }
            _ => {
                // For other effects, just break to avoid complexity
                break;
            }
        };
    }

    // Test passes if we exit without detecting an infinite loop
}

use crate::reducer::state::FixStatus;

/// Simulates running the event loop to verify fix continuation does not cause infinite loops.
///
/// This test starts with a state that has `fix_continue_pending=true` and runs
/// through the Review phase fix chain to verify the pipeline progresses correctly.
#[test]
fn test_fix_continuation_does_not_cause_infinite_loop_in_event_loop_simulation() {
    let mut state = PipelineState {
        phase: PipelinePhase::Review,
        reviewer_pass: 0,
        total_reviewer_passes: 2,
        review_issues_found: true,
        agent_chain: AgentChainState::initial()
            .with_agents(
                vec!["claude".to_string()],
                vec![vec![]],
                AgentRole::Reviewer,
            )
            .with_drain(crate::agents::AgentDrain::Fix),
        continuation: ContinuationState {
            fix_continue_pending: true,
            fix_continuation_attempt: 1,
            ..ContinuationState::default()
        },
        fix_prompt_prepared_pass: None,
        ..PipelineState::initial(5, 2)
    };

    let max_iterations = 100;
    let mut last_effect_discriminant = None;
    let mut repeat_count = 0;

    for i in 0..max_iterations {
        let effect = determine_next_effect(&state);
        let current_discriminant = std::mem::discriminant(&effect);

        // Track consecutive repeats of the same effect type
        if Some(current_discriminant) == last_effect_discriminant {
            repeat_count += 1;
            assert!(repeat_count <= 5,
                "Potential infinite loop at iteration {i}: effect {effect:?} repeated {repeat_count} times"
            );
        } else {
            repeat_count = 1;
            last_effect_discriminant = Some(current_discriminant);
        }

        // Simulate applying the effect by reducing the corresponding event
        state = match effect {
            Effect::LockPromptPermissions => {
                reduce(state, PipelineEvent::prompt_permissions_locked(None))
            }
            Effect::RestorePromptPermissions => {
                reduce(state, PipelineEvent::prompt_permissions_restored())
            }
            Effect::PrepareFixPrompt { pass, .. } => {
                reduce(state, PipelineEvent::fix_prompt_prepared(pass))
            }
            Effect::CleanupRequiredFiles { files }
                if files.iter().any(|f| f.contains("fix_result.xml")) =>
            {
                let pass = state.reviewer_pass;
                reduce(state, PipelineEvent::fix_result_xml_cleaned(pass))
            }
            Effect::InvokeFixAgent { pass } => {
                reduce(state, PipelineEvent::fix_agent_invoked(pass))
            }
            Effect::ExtractFixResultXml { pass } => {
                reduce(state, PipelineEvent::fix_result_xml_extracted(pass))
            }
            Effect::ValidateFixResultXml { pass } => reduce(
                state,
                PipelineEvent::fix_result_xml_validated(
                    pass,
                    FixStatus::AllIssuesAddressed,
                    Some("All issues resolved".to_string()),
                ),
            ),
            Effect::ArchiveFixResultXml { pass } => {
                reduce(state, PipelineEvent::fix_result_xml_archived(pass))
            }
            Effect::ApplyFixOutcome { pass } => {
                reduce(state, PipelineEvent::fix_outcome_applied(pass))
            }
            Effect::SaveCheckpoint { .. } => {
                // Phase complete - success!
                break;
            }
            _ => {
                // For other effects, just break to avoid complexity
                break;
            }
        };
    }

    // Verify fix_continue_pending was cleared
    assert!(
        !state.continuation.fix_continue_pending,
        "fix_continue_pending should be false after FixPromptPrepared"
    );
}
