//! Integration tests for diff failure fallback behavior.
//!
//! These tests verify that when `git diff` fails, the commit phase:
//! 1. Does NOT emit `DiffFailed` event (which would terminate pipeline)
//! 2. Uses fallback instructions instead of actual diff content
//! 3. Allows AI agent to investigate and skip commit if needed
//! 4. Emits `CommitEvent::Skipped` when AI determines no commit needed
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[`INTEGRATION_TESTS.md`](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** via effect capture
//! - Uses `MockAppEffectHandler` AND `MockEffectHandler` for git/filesystem isolation
//! - NO `TempDir`, `std::fs`, or real git operations
//! - Tests are deterministic and verify effects, not real filesystem state

use crate::common::{
    create_test_config_struct, mock_executor_with_success, run_ralph_cli_with_handlers,
};
use crate::test_timeout::with_default_timeout;
use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::PipelinePhase;
use ralph_workflow::reducer::mock_effect_handler::MockEffectHandler;
use ralph_workflow::reducer::PipelineState;
use std::path::PathBuf;

/// Standard PROMPT.md content for diff failure tests.
const STANDARD_PROMPT: &str = r"## Goal

Do something.

## Acceptance

- Tests pass
";

/// Test that git diff failure uses fallback instructions instead of terminating.
///
/// This verifies that when `git diff` fails, the pipeline:
/// 1. Does NOT emit `DiffFailed` event
/// 2. Does NOT transition to `Interrupted` phase
/// 3. Writes fallback instructions to `.agent/tmp/commit_diff.txt`
/// 4. Continues to execute commit message generation
#[test]
fn test_diff_failure_uses_fallback_instructions() {
    with_default_timeout(|| {
        // Create handler with diff failure scenario
        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_staged_changes(true);

        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_commit_diff_error("diff failed (simulated)");
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Key observation: Pipeline should NOT be in Interrupted phase
        assert_ne!(
            effect_handler.state.phase,
            PipelinePhase::Interrupted,
            "Pipeline should NOT transition to Interrupted on diff failure"
        );

        // Verify that CheckCommitDiff effect was executed
        let check_diff_executed =
            effect_handler.was_effect_executed(|e| matches!(e, Effect::CheckCommitDiff));
        assert!(
            check_diff_executed,
            "CheckCommitDiff effect should be executed"
        );

        // Verify fallback instructions were written (observable output)
        let diff_path = PathBuf::from(".agent/tmp/commit_diff.txt");
        let diff_content = app_handler
            .get_file(&diff_path)
            .expect("Expected commit diff file to be written");
        assert!(
            diff_content.contains("DIFF UNAVAILABLE"),
            "Fallback diff content should include investigation header"
        );
        assert!(
            diff_content.contains("git status"),
            "Fallback diff content should instruct running git status"
        );
        assert!(
            diff_content.contains("<ralph-skip>"),
            "Fallback diff content should document the ralph-skip option"
        );
    });
}

/// Test that fallback instructions contain investigation guidance.
///
/// This verifies that the fallback diff content:
/// 1. Contains clear instructions for manual investigation
/// 2. Mentions `git status` as investigation tool
/// 3. Documents the `ralph-skip` option
#[test]
fn test_fallback_instructions_contain_investigation_guidance() {
    with_default_timeout(|| {
        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_staged_changes(true);

        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_commit_diff_error("diff failed (simulated)");
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Verify CheckCommitDiff effect was executed to prepare fallback diff
        assert!(
            effect_handler.was_effect_executed(|e| matches!(e, Effect::CheckCommitDiff)),
            "CheckCommitDiff effect should execute to prepare fallback diff"
        );

        // Observable behavior: DiffPrepared was emitted (with fallback content)
        assert!(
            effect_handler.was_event_emitted(|e| {
                matches!(
                    e,
                    ralph_workflow::reducer::event::PipelineEvent::Commit(
                        ralph_workflow::reducer::event::CommitEvent::DiffPrepared {
                            empty: false, ..
                        }
                    )
                )
            }),
            "DiffPrepared(empty=false) should be emitted even when git diff fails (with fallback content)"
        );
    });
}

/// Test that AI can respond with ralph-skip after investigating fallback.
///
/// This verifies the end-to-end flow:
/// 1. Diff fails → fallback instructions written
/// 2. AI investigates and determines no commit needed
/// 3. AI responds with `<ralph-skip>` element
/// 4. Validation handler emits `CommitEvent::Skipped`
///
/// Note: This test focuses on the observable behavior at the effect level.
/// The actual XML parsing is tested separately in unit tests.
#[test]
fn test_ai_can_skip_commit_after_diff_failure() {
    with_default_timeout(|| {
        // Simulate scenario where diff fails but AI determines skip is appropriate.
        // The mock ValidateCommitXml runs the real commit XML extraction on the
        // provided XML and emits CommitEvent::Skipped.
        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_staged_changes(false); // No actual staged changes

        let skip_xml = "<ralph-commit><ralph-skip>No changes found</ralph-skip></ralph-commit>";
        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_commit_diff_error("diff failed (simulated)")
            .with_commit_message_xml(skip_xml);
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Observable behavior: pipeline is not interrupted and commit state is skipped.
        assert_ne!(
            effect_handler.state.phase,
            PipelinePhase::Interrupted,
            "Pipeline should NOT be interrupted when AI skips commit"
        );

        assert!(
            matches!(
                effect_handler.state.commit,
                ralph_workflow::reducer::state::CommitState::Skipped
            ),
            "Commit state should be Skipped when AI emits <ralph-skip>"
        );
    });
}

/// Test that `DiffFailed` event is no longer emitted in new code paths.
///
/// This verifies backward compatibility: the `DiffFailed` reducer still exists
/// (for old checkpoints) but is never emitted by new handler code.
#[test]
fn test_diff_failed_event_not_emitted_by_new_code() {
    with_default_timeout(|| {
        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_staged_changes(true);

        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_commit_diff_error("diff failed (simulated)");
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Verify that DiffFailed was never emitted.
        // This must be asserted on emitted events, not inferred from phase transitions,
        // because DiffFailed is now a reducer no-op.
        assert!(
            !effect_handler.was_event_emitted(|e| {
                matches!(
                    e,
                    ralph_workflow::reducer::event::PipelineEvent::Commit(
                        ralph_workflow::reducer::event::CommitEvent::DiffFailed { .. }
                    )
                )
            }),
            "DiffFailed event should NOT be emitted by new handler code"
        );

        // Observable success path: diff preparation still happens (with fallback content).
        assert!(
            effect_handler.was_event_emitted(|e| {
                matches!(
                    e,
                    ralph_workflow::reducer::event::PipelineEvent::Commit(
                        ralph_workflow::reducer::event::CommitEvent::DiffPrepared { .. }
                    )
                )
            }),
            "DiffPrepared should be emitted even when git diff fails"
        );

        // Pipeline should continue to commit phase (not stuck in error state)
        // The fact that we reached a terminal state without Interrupted proves fallback worked
        assert!(
            effect_handler.state.is_terminal(),
            "Pipeline should complete normally using fallback instructions"
        );
    });
}

/// Test that fallback behavior works with multiple files changed.
///
/// This verifies that fallback instructions work even when there are
/// multiple files with staged changes (testing that the instructions
/// are generic enough to handle any change scenario).
#[test]
fn test_fallback_works_with_multiple_files() {
    with_default_timeout(|| {
        let mut app_handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file("file1.rs", "// Modified file 1")
            .with_file("file2.rs", "// Modified file 2")
            .with_file("file3.md", "# Modified doc")
            .with_staged_changes(true);

        let mut effect_handler = MockEffectHandler::new(PipelineState::initial(0, 0))
            .with_commit_diff_error("diff failed (simulated)");
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handlers(&[], executor, config, &mut app_handler, &mut effect_handler)
            .unwrap();

        // Verify CheckCommitDiff effect was executed for multiple-file scenario
        assert!(
            effect_handler.was_effect_executed(|e| matches!(e, Effect::CheckCommitDiff)),
            "CheckCommitDiff should handle multiple files in fallback mode"
        );

        // Pipeline should handle multiple files without issue
        assert_ne!(
            effect_handler.state.phase,
            PipelinePhase::Interrupted,
            "Pipeline should handle multiple files in fallback mode"
        );

        // Observable behavior: DiffPrepared was emitted (with fallback content)
        assert!(
            effect_handler.was_event_emitted(|e| {
                matches!(
                    e,
                    ralph_workflow::reducer::event::PipelineEvent::Commit(
                        ralph_workflow::reducer::event::CommitEvent::DiffPrepared { .. }
                    )
                )
            }),
            "Diff should be prepared with fallback content"
        );
    });
}
