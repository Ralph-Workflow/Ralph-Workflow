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
        !new_state.pre_termination_commit_checked,
        "Safety commit completion must NOT auto-unblock termination; the pre-termination \
         safety check must re-run to confirm the repo is clean"
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

// =========================================================================
// Tests for residual files and second-pass commit logic
// =========================================================================

#[test]
fn test_commit_xml_validated_stores_excluded_files() {
    // CommitXmlValidated with excluded_files must store them in commit_excluded_files.
    use crate::reducer::state::pipeline::{ExcludedFile, ExcludedFileReason};

    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;

    let excluded = vec![ExcludedFile {
        path: "src/sensitive.rs".to_string(),
        reason: ExcludedFileReason::Sensitive,
    }];

    let event = PipelineEvent::commit_xml_validated(
        "feat: add feature".to_string(),
        vec!["src/main.rs".to_string()],
        excluded.clone(),
        1,
    );
    let new_state = reduce(state, event);

    assert_eq!(
        new_state.commit_excluded_files, excluded,
        "CommitXmlValidated must store excluded_files in commit_excluded_files"
    );
}

#[test]
fn test_commit_xml_validated_empty_excluded_files() {
    // CommitXmlValidated with no excluded files stores empty vec.
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;

    let event =
        PipelineEvent::commit_xml_validated("feat: add feature".to_string(), vec![], vec![], 1);
    let new_state = reduce(state, event);

    assert!(
        new_state.commit_excluded_files.is_empty(),
        "Empty excluded_files must produce empty commit_excluded_files"
    );
}

#[test]
fn test_residual_files_found_pass1_sets_second_pass_flag() {
    // ResidualFilesFound pass=1: pipeline must enter a second commit pass.
    // commit_is_second_pass must be set to true and commit state must be reset
    // so orchestration can run a fresh second commit cycle.
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.commit_is_second_pass = false;

    let event = PipelineEvent::residual_files_found(vec!["src/leftover.rs".to_string()], 1);
    let new_state = reduce(state, event);

    assert!(
        new_state.commit_is_second_pass,
        "ResidualFilesFound pass=1 must set commit_is_second_pass=true"
    );
    // commit state must be reset for the second pass
    assert!(
        !new_state.commit_diff_prepared,
        "commit_diff_prepared must be reset for second pass"
    );
    assert!(
        !new_state.commit_agent_invoked,
        "commit_agent_invoked must be reset for second pass"
    );
    assert!(
        !new_state.commit_prompt_prepared,
        "commit_prompt_prepared must be reset for second pass"
    );
    // residual files must NOT yet be moved to carry-forward
    assert!(
        new_state.commit_residual_files.is_empty(),
        "commit_residual_files must stay empty after pass=1 (not yet carry-forward)"
    );
}

#[test]
fn test_residual_files_found_pass2_carries_forward() {
    // ResidualFilesFound pass=2: files remaining after the second pass must be
    // stored in commit_residual_files for carry-forward to the next cycle.
    // commit_is_second_pass must be cleared and commit state must reset normally.
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.commit_is_second_pass = true;

    let residual = vec!["src/remaining.rs".to_string(), "tests/other.rs".to_string()];
    let event = PipelineEvent::residual_files_found(residual.clone(), 2);
    let new_state = reduce(state, event);

    assert_eq!(
        new_state.commit_residual_files, residual,
        "ResidualFilesFound pass=2 must store files in commit_residual_files"
    );
    assert!(
        !new_state.commit_is_second_pass,
        "commit_is_second_pass must be cleared after pass=2"
    );
}

#[test]
fn test_residual_files_none_clears_second_pass_and_residual() {
    // ResidualFilesNone: working tree is clean after a commit pass.
    // commit_is_second_pass must be cleared and commit_residual_files must be cleared.
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.commit_is_second_pass = true;
    state.commit_residual_files = vec!["src/old.rs".to_string()];

    let event = PipelineEvent::residual_files_none();
    let new_state = reduce(state, event);

    assert!(
        !new_state.commit_is_second_pass,
        "ResidualFilesNone must clear commit_is_second_pass"
    );
    assert!(
        new_state.commit_residual_files.is_empty(),
        "ResidualFilesNone must clear commit_residual_files"
    );
}

#[test]
fn test_residual_files_found_invalid_pass_routes_to_awaiting_dev_fix() {
    // ResidualFilesFound must only accept pass=1 or pass=2.
    // Any other value indicates an invariant violation; the reducer must route
    // through AwaitingDevFix so unattended remediation can proceed deterministically.
    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::CommitMessage;

    let event = PipelineEvent::residual_files_found(vec!["src/leftover.rs".to_string()], 0);
    let new_state = reduce(state, event);

    assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    assert_eq!(
        new_state.failed_phase_for_recovery,
        Some(PipelinePhase::CommitMessage)
    );
    assert_eq!(new_state.previous_phase, Some(PipelinePhase::CommitMessage));
    assert!(
        !new_state.commit_is_second_pass,
        "invalid pass must not trigger second-pass behavior"
    );
    assert!(
        new_state.commit_residual_files.is_empty(),
        "invalid pass must not silently carry-forward residuals"
    );
}

#[test]
fn test_commit_residual_files_survives_generation_started() {
    // commit_residual_files is carry-forward state: it must NOT be cleared when
    // GenerationStarted resets the commit phase for a new cycle.
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.commit_residual_files = vec!["src/leftover.rs".to_string()];

    let event = PipelineEvent::commit_generation_started();
    let new_state = reduce(state, event);

    assert_eq!(
        new_state.commit_residual_files,
        vec!["src/leftover.rs".to_string()],
        "commit_residual_files must survive GenerationStarted (carry-forward across cycles)"
    );
}

#[test]
fn test_commit_residual_files_cleared_after_commit_created() {
    // After a successful commit completes and transitions to the next phase,
    // commit_residual_files must be cleared (the files were presumably committed).
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.previous_phase = Some(PipelinePhase::Development);
    state.iteration = 0;
    state.commit_residual_files = vec!["src/leftover.rs".to_string()];

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "feat: done".to_string()),
    );

    assert!(
        new_state.commit_residual_files.is_empty(),
        "commit_residual_files must be cleared after CommitEvent::Created"
    );
}

#[test]
fn test_selective_commit_created_stays_in_commit_message_until_residual_check_completes() {
    // Regression: when the commit is selective (commit_selected_files is non-empty), the
    // pipeline must remain in CommitMessage after the commit is created so orchestration
    // can run CheckResidualFiles and (if needed) a second commit pass.
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.previous_phase = Some(PipelinePhase::Development);
    state.iteration = 0;
    state.commit_selected_files = vec!["src/one.rs".to_string()];

    let new_state = reduce(
        state,
        PipelineEvent::commit_created("abc123".to_string(), "msg".to_string()),
    );

    assert_eq!(
        new_state.phase,
        PipelinePhase::CommitMessage,
        "Selective commit must keep phase in CommitMessage until residual checking completes"
    );
    assert_eq!(
        new_state.previous_phase,
        Some(PipelinePhase::Development),
        "Selective commit must preserve previous_phase until post-commit transition occurs"
    );
}

#[test]
fn test_residual_files_none_transitions_after_selective_commit() {
    // ResidualFilesNone marks the end of residual handling; the pipeline must now perform
    // the normal post-commit transition (e.g., Development -> Planning for next iteration).
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.previous_phase = Some(PipelinePhase::Development);
    state.iteration = 0;
    state.commit = CommitState::Committed {
        hash: "abc123".to_string(),
    };
    state.commit_selected_files = vec!["src/one.rs".to_string()];

    let new_state = reduce(state, PipelineEvent::residual_files_none());

    assert_eq!(
        new_state.phase,
        PipelinePhase::Planning,
        "After residuals are clean, pipeline should transition out of CommitMessage"
    );
    assert_eq!(
        new_state.iteration, 1,
        "Post-commit transition from Development should increment iteration"
    );
    assert!(
        new_state.previous_phase.is_none(),
        "Post-commit transition must clear previous_phase"
    );
}

#[test]
fn test_residual_files_found_pass2_transitions_and_carries_forward_after_second_pass() {
    // If pass 2 still has leftovers, carry them forward and transition out of CommitMessage.
    let mut state = PipelineState::initial(2, 0);
    state.phase = PipelinePhase::CommitMessage;
    state.previous_phase = Some(PipelinePhase::Development);
    state.iteration = 0;
    state.commit = CommitState::Committed {
        hash: "abc123".to_string(),
    };
    state.commit_is_second_pass = true;
    state.commit_selected_files = vec!["src/one.rs".to_string()];

    let residual = vec!["src/remaining.rs".to_string()];
    let new_state = reduce(
        state,
        PipelineEvent::residual_files_found(residual.clone(), 2),
    );

    assert_eq!(new_state.commit_residual_files, residual);
    assert_eq!(
        new_state.phase,
        PipelinePhase::Planning,
        "After pass 2 residuals are recorded, pipeline should transition out of CommitMessage"
    );
    assert_eq!(new_state.iteration, 1);
}
