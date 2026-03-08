//! Commit phase reducer tests.
//!
//! Tests for commit-related event handling in the state reduction layer.

use crate::reducer::event::*;
use crate::reducer::state::*;
use crate::reducer::state_reduction::reduce;

#[test]
fn test_diff_failed_event_is_noop_for_backward_compatibility() {
    // DiffFailed is deprecated and should not be emitted by current handler code.
    // If received (e.g., from old checkpoint), it should be a no-op to avoid termination.

    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.commit_diff_prepared = true;
    state.commit_diff_empty = false;
    state.commit_diff_content_id_sha256 = Some("abc123".to_string());

    let event = PipelineEvent::commit_diff_failed("git diff failed".to_string());
    let new_state = reduce(state.clone(), event);

    // Event should be no-op: state unchanged
    assert_eq!(new_state.phase, state.phase);
    assert_eq!(new_state.commit_diff_prepared, state.commit_diff_prepared);
    assert_eq!(new_state.commit_diff_empty, state.commit_diff_empty);
    assert_eq!(
        new_state.commit_diff_content_id_sha256,
        state.commit_diff_content_id_sha256
    );

    // Should NOT transition to Interrupted
    assert_ne!(new_state.phase, PipelinePhase::Interrupted);
}

#[test]
fn test_diff_prepared_event_sets_flags() {
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;

    let event = PipelineEvent::commit_diff_prepared(false, "content_hash".to_string());
    let new_state = reduce(state, event);

    assert!(new_state.commit_diff_prepared);
    assert!(!new_state.commit_diff_empty);
    assert_eq!(
        new_state.commit_diff_content_id_sha256,
        Some("content_hash".to_string())
    );
}

#[test]
fn test_diff_prepared_empty_sets_empty_flag() {
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;

    let event = PipelineEvent::commit_diff_prepared(true, "empty_hash".to_string());
    let new_state = reduce(state, event);

    assert!(new_state.commit_diff_prepared);
    assert!(new_state.commit_diff_empty);
    assert_eq!(
        new_state.commit_diff_content_id_sha256,
        Some("empty_hash".to_string())
    );
}

#[test]
fn test_diff_invalidated_clears_flags() {
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.commit_diff_prepared = true;
    state.commit_diff_empty = false;
    state.commit_diff_content_id_sha256 = Some("old_hash".to_string());
    state.commit_prompt_prepared = true;

    let event = PipelineEvent::commit_diff_invalidated("Diff file missing".to_string());
    let new_state = reduce(state, event);

    assert!(!new_state.commit_diff_prepared);
    assert!(!new_state.commit_diff_empty);
    assert_eq!(new_state.commit_diff_content_id_sha256, None);
    assert!(!new_state.commit_prompt_prepared);
}

#[test]
fn test_pre_termination_uncommitted_changes_routes_back_to_commit_phase() {
    // When the pre-termination safety check finds uncommitted changes, the reducer must
    // route back through the commit phase (unattended-mode safety), recording the
    // phase we should resume after committing.
    let mut state = PipelineState::initial(0, 0);
    state.phase = PipelinePhase::Complete;
    state.pre_termination_commit_checked = false;
    state.termination_resume_phase = None;

    let event = PipelineEvent::pre_termination_uncommitted_changes_detected(3);
    let new_state = reduce(state, event);

    assert_eq!(new_state.phase, PipelinePhase::CommitMessage);
    assert_eq!(
        new_state.termination_resume_phase,
        Some(PipelinePhase::Complete)
    );
}

#[test]
fn test_post_commit_resumes_termination_phase_when_safety_commit_pending() {
    // If we routed into CommitMessage due to the pre-termination safety check,
    // a successful commit must resume the original termination phase and allow
    // termination to proceed.
    let mut state = PipelineState::initial(0, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.termination_resume_phase = Some(PipelinePhase::Complete);
    state.pre_termination_commit_checked = false;

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "msg".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::Complete);
    assert_eq!(new_state.termination_resume_phase, None);
    assert!(
        new_state.pre_termination_commit_checked,
        "Termination should be unblocked after safety commit completes"
    );
}

#[test]
fn test_skip_does_not_unblock_termination_when_safety_commit_pending() {
    // If the pre-termination safety check detected a dirty repo and routed into CommitMessage,
    // an AI-driven "skip" must NOT unblock termination.
    //
    // The pipeline must re-run CheckUncommittedChangesBeforeTermination after any skip and only
    // proceed once the repo is actually clean.
    let mut state = PipelineState::initial(0, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.termination_resume_phase = Some(PipelinePhase::Complete);
    state.pre_termination_commit_checked = false;

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("no changes".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::Complete);
    assert_eq!(new_state.termination_resume_phase, None);
    assert!(
        !new_state.pre_termination_commit_checked,
        "Skip during safety-check commit must not unblock termination"
    );
}

#[test]
fn test_empty_diff_skip_unblocks_termination_when_safety_commit_pending() {
    // When the pre-termination safety check detected a dirty repo and routed into
    // CommitMessage, but the diff is empty (orchestration-initiated skip, not AI-driven),
    // the repo has nothing to commit. The skip MUST unblock termination to prevent an
    // infinite loop: safety check → commit phase → empty diff → skip → safety check → ...
    let mut state = PipelineState::initial(0, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.termination_resume_phase = Some(PipelinePhase::Complete);
    state.pre_termination_commit_checked = false;
    state.commit_diff_empty = true; // Orchestration detected empty diff

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("No changes to commit (empty diff)".to_string()),
    );

    assert_eq!(new_state.phase, PipelinePhase::Complete);
    assert_eq!(new_state.termination_resume_phase, None);
    assert!(
        new_state.pre_termination_commit_checked,
        "Empty-diff skip during safety-check commit must unblock termination \
         to prevent infinite loop"
    );
}

#[test]
fn test_created_normal_resets_commit_diff_prepared() {
    // After a successful commit (normal path), commit_diff_prepared must be reset to false
    // so the next entry into the commit phase always re-checks the diff.
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.previous_phase = Some(PipelinePhase::Development);
    state.iteration = 0;
    state.commit_diff_prepared = true;
    state.commit_diff_empty = false;
    state.commit_diff_content_id_sha256 = Some("hash-to-reset".to_string());
    // termination_resume_phase is None by default => normal path

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "fix: something".to_string()),
    );

    assert!(
        !new_state.commit_diff_prepared,
        "commit_diff_prepared must be false after CommitEvent::Created (normal path)"
    );
    assert!(
        !new_state.commit_diff_empty,
        "commit_diff_empty must be false after CommitEvent::Created (normal path)"
    );
    assert_eq!(
        new_state.commit_diff_content_id_sha256, None,
        "commit_diff_content_id_sha256 must be None after CommitEvent::Created (normal path)"
    );
}

#[test]
fn test_created_pre_termination_resets_commit_diff_prepared() {
    // After a successful commit in the pre-termination safety path, commit_diff_prepared must
    // be reset to false so the next commit-phase entry always re-checks the diff.
    let mut state = PipelineState::initial(0, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.termination_resume_phase = Some(PipelinePhase::Complete);
    state.pre_termination_commit_checked = false;
    state.commit_diff_prepared = true;
    state.commit_diff_empty = false;
    state.commit_diff_content_id_sha256 = Some("hash-to-reset".to_string());

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "fix: something".to_string()),
    );

    assert!(
        !new_state.commit_diff_prepared,
        "commit_diff_prepared must be false after CommitEvent::Created (pre-termination path)"
    );
    assert!(
        !new_state.commit_diff_empty,
        "commit_diff_empty must be false after CommitEvent::Created (pre-termination path)"
    );
    assert_eq!(
        new_state.commit_diff_content_id_sha256, None,
        "commit_diff_content_id_sha256 must be None after CommitEvent::Created (pre-termination path)"
    );
}

#[test]
fn test_skipped_normal_resets_commit_diff_prepared() {
    // After a skipped commit (normal path), commit_diff_prepared must be reset to false
    // so the next entry into the commit phase always re-checks the diff.
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.previous_phase = Some(PipelinePhase::Development);
    state.iteration = 0;
    state.commit_diff_prepared = true;
    state.commit_diff_empty = false;
    state.commit_diff_content_id_sha256 = Some("hash-to-reset".to_string());
    // termination_resume_phase is None by default => normal path

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("AI chose to skip".to_string()),
    );

    assert!(
        !new_state.commit_diff_prepared,
        "commit_diff_prepared must be false after CommitEvent::Skipped (normal path)"
    );
    assert!(
        !new_state.commit_diff_empty,
        "commit_diff_empty must be false after CommitEvent::Skipped (normal path)"
    );
    assert_eq!(
        new_state.commit_diff_content_id_sha256, None,
        "commit_diff_content_id_sha256 must be None after CommitEvent::Skipped (normal path)"
    );
}

#[test]
fn test_skipped_pre_termination_resets_commit_diff_prepared() {
    // After a skipped commit in the pre-termination safety path (AI-driven skip with
    // non-empty diff), commit_diff_prepared must be reset to false.
    let mut state = PipelineState::initial(0, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.termination_resume_phase = Some(PipelinePhase::Complete);
    state.pre_termination_commit_checked = false;
    state.commit_diff_prepared = true;
    state.commit_diff_empty = false; // non-empty => AI-driven skip, pre_termination_commit_checked stays false
    state.commit_diff_content_id_sha256 = Some("hash-to-reset".to_string());

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("AI chose to skip".to_string()),
    );

    assert!(
        !new_state.commit_diff_prepared,
        "commit_diff_prepared must be false after CommitEvent::Skipped (pre-termination path)"
    );
    assert!(
        !new_state.commit_diff_empty,
        "commit_diff_empty must be false after CommitEvent::Skipped (pre-termination path)"
    );
    assert_eq!(
        new_state.commit_diff_content_id_sha256, None,
        "commit_diff_content_id_sha256 must be None after CommitEvent::Skipped (pre-termination path)"
    );
}

#[test]
fn test_created_normal_clears_prompt_inputs_commit() {
    // After a successful commit (normal path), prompt_inputs.commit must be cleared
    // to prevent stale materialized commit diff/context from surviving into the next iteration.
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.previous_phase = Some(PipelinePhase::Development);
    state.iteration = 0;
    state.commit_diff_prepared = true;
    // Set stale commit inputs that must be cleared after commit
    state.prompt_inputs.commit = Some(MaterializedCommitInputs {
        attempt: 1,
        diff: MaterializedPromptInput {
            kind: PromptInputKind::Diff,
            content_id_sha256: "stale-hash".to_string(),
            consumer_signature_sha256: "stale-sig".to_string(),
            original_bytes: 100,
            final_bytes: 100,
            model_budget_bytes: None,
            inline_budget_bytes: None,
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        },
    });
    // termination_resume_phase is None by default => normal path

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "fix: something".to_string()),
    );

    assert!(
        new_state.prompt_inputs.commit.is_none(),
        "prompt_inputs.commit must be cleared after CommitEvent::Created (normal path) \
         to prevent stale commit context reuse in later iterations"
    );
}

#[test]
fn test_created_pre_termination_clears_prompt_inputs_commit() {
    // After a successful commit (pre-termination safety path), prompt_inputs.commit must be
    // cleared to prevent stale materialized commit context from surviving.
    let mut state = PipelineState::initial(0, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.termination_resume_phase = Some(PipelinePhase::Complete);
    state.pre_termination_commit_checked = false;
    state.commit_diff_prepared = true;
    state.prompt_inputs.commit = Some(MaterializedCommitInputs {
        attempt: 1,
        diff: MaterializedPromptInput {
            kind: PromptInputKind::Diff,
            content_id_sha256: "stale-hash".to_string(),
            consumer_signature_sha256: "stale-sig".to_string(),
            original_bytes: 100,
            final_bytes: 100,
            model_budget_bytes: None,
            inline_budget_bytes: None,
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        },
    });

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "fix: something".to_string()),
    );

    assert!(
        new_state.prompt_inputs.commit.is_none(),
        "prompt_inputs.commit must be cleared after CommitEvent::Created (pre-termination path)"
    );
}

#[test]
fn test_skipped_normal_clears_prompt_inputs_commit() {
    // After a skipped commit (normal path), prompt_inputs.commit must be cleared
    // to prevent stale materialized commit diff/context from surviving into the next iteration.
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.previous_phase = Some(PipelinePhase::Development);
    state.iteration = 0;
    state.commit_diff_prepared = true;
    state.prompt_inputs.commit = Some(MaterializedCommitInputs {
        attempt: 1,
        diff: MaterializedPromptInput {
            kind: PromptInputKind::Diff,
            content_id_sha256: "stale-hash".to_string(),
            consumer_signature_sha256: "stale-sig".to_string(),
            original_bytes: 100,
            final_bytes: 100,
            model_budget_bytes: None,
            inline_budget_bytes: None,
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        },
    });
    // termination_resume_phase is None by default => normal path

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("AI chose to skip".to_string()),
    );

    assert!(
        new_state.prompt_inputs.commit.is_none(),
        "prompt_inputs.commit must be cleared after CommitEvent::Skipped (normal path) \
         to prevent stale commit context reuse in later iterations"
    );
}

#[test]
fn test_diff_prepared_clears_stale_prompt_inputs_surviving_generation_failed() {
    // When GenerationFailed fires, prompt_inputs.commit is NOT cleared (uses ..state spread).
    // The subsequent DiffPrepared event MUST clear it to force rematerialization before the
    // next commit prompt is prepared. This is the canonical safety net for diff freshness.
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::CommitMessage;
    // Simulate stale commit inputs materialized during iteration 1
    state.prompt_inputs.commit = Some(MaterializedCommitInputs {
        attempt: 1,
        diff: MaterializedPromptInput {
            kind: PromptInputKind::Diff,
            content_id_sha256: "iter-1-hash".to_string(),
            consumer_signature_sha256: "iter-1-sig".to_string(),
            original_bytes: 100,
            final_bytes: 100,
            model_budget_bytes: None,
            inline_budget_bytes: None,
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        },
    });

    // Step 1: GenerationFailed should NOT clear prompt_inputs.commit
    let after_failed = reduce(
        state,
        PipelineEvent::commit_generation_failed("agent timeout".to_string()),
    );
    assert!(
        after_failed.prompt_inputs.commit.is_some(),
        "GenerationFailed must NOT clear prompt_inputs.commit — DiffPrepared is the safety net"
    );

    // Step 2: DiffPrepared (from the re-triggered CheckCommitDiff) MUST clear stale inputs
    let after_diff_prepared = reduce(
        after_failed,
        PipelineEvent::commit_diff_prepared(false, "iter-2-hash".to_string()),
    );
    assert!(
        after_diff_prepared.prompt_inputs.commit.is_none(),
        "DiffPrepared must clear prompt_inputs.commit to prevent stale diff context reuse"
    );
}

#[test]
fn test_skipped_pre_termination_clears_prompt_inputs_commit() {
    // After a skipped commit (pre-termination safety path), prompt_inputs.commit must be
    // cleared to prevent stale materialized commit context from surviving.
    let mut state = PipelineState::initial(0, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.termination_resume_phase = Some(PipelinePhase::Complete);
    state.pre_termination_commit_checked = false;
    state.commit_diff_prepared = true;
    state.commit_diff_empty = false; // non-empty => AI-driven skip
    state.prompt_inputs.commit = Some(MaterializedCommitInputs {
        attempt: 1,
        diff: MaterializedPromptInput {
            kind: PromptInputKind::Diff,
            content_id_sha256: "stale-hash".to_string(),
            consumer_signature_sha256: "stale-sig".to_string(),
            original_bytes: 100,
            final_bytes: 100,
            model_budget_bytes: None,
            inline_budget_bytes: None,
            representation: PromptInputRepresentation::Inline,
            reason: PromptMaterializationReason::WithinBudgets,
        },
    });

    let new_state = reduce(
        state,
        PipelineEvent::commit_skipped("AI chose to skip".to_string()),
    );

    assert!(
        new_state.prompt_inputs.commit.is_none(),
        "prompt_inputs.commit must be cleared after CommitEvent::Skipped (pre-termination path)"
    );
}
