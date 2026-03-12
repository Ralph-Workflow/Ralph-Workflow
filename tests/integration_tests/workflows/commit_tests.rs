//! Commit behavior integration tests.
//!
//! These tests verify that commit operations work correctly across
//! different scenarios and configurations.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (effects captured, error returns)
//! - Uses `MockAppEffectHandler` to mock at architectural boundary (filesystem/git)
//! - Pipeline tests use **dependency injection** via `create_test_config_struct()`
//! - Tests are deterministic and isolated
//!
//! # Note on Real Git Tests
//!
//! Tests that verify actual git2 commit creation belong in `tests/system_tests/commit/`
//! as they require real git repository operations.

use std::path::PathBuf;

use super::resume::{make_checkpoint_json, MOCK_REPO_PATH};
use ralph_workflow::app::effect::AppEffect;
use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;

use crate::common::{
    create_test_config_struct, mock_executor_with_success, run_ralph_cli_with_handler,
};
use crate::test_timeout::with_default_timeout;

/// Standard prompt content for tests - matches the required PROMPT.md format.
const STANDARD_PROMPT: &str = r"## Goal

Test the Ralph workflow integration

## Acceptance

- Tests pass
";

// ============================================================================
// Commit Behavior Tests
// ============================================================================

/// Test that the pipeline succeeds without a pre-existing commit message file.
///
/// This verifies that when a user runs ralph without a commit-message.txt file,
/// the pipeline still succeeds using auto-commit behavior which generates
/// a commit message automatically.
#[test]
fn ralph_succeeds_without_commit_message_file() {
    with_default_timeout(|| {
        // With auto-commit behavior, the pipeline should succeed even without
        // a pre-existing commit-message.txt file since commits are created
        // automatically by the orchestrator using the commit message generation.
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file("test.txt", "test content");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Should succeed - auto-commit will generate a message
        run_ralph_cli_with_handler(&[], executor, config, &mut handler).unwrap();
    });
}

// ============================================================================
// Plumbing Command Tests
// ============================================================================

/// Test that the `--show-commit-msg` flag displays the commit message.
///
/// This verifies that when a user invokes ralph with the `--show-commit-msg` flag
/// and a commit-message.txt file exists, the command succeeds.
///
/// Note: Plumbing commands bypass config loading entirely, so we use the
/// handler to set up the required file state.
#[test]
fn ralph_show_commit_msg_displays_message() {
    with_default_timeout(|| {
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file(".agent/commit-message.txt", "feat: test commit message\n");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();
        run_ralph_cli_with_handler(&["--show-commit-msg"], executor, config, &mut handler).unwrap();
    });
}

/// Test that the `--show-commit-msg` flag reads from the specified repo root.
///
/// This verifies that when a user invokes ralph with the `--show-commit-msg` flag
/// and specifies a working directory, the command reads the commit-message.txt
/// from that directory regardless of where subdirectories might have their own files.
///
/// Note: Plumbing commands bypass config loading entirely, so we use the
/// handler to set up the required file state.
#[test]
fn ralph_show_commit_msg_reads_from_working_dir() {
    with_default_timeout(|| {
        // Root commit message (the one we expect to read)
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file(".agent/commit-message.txt", "feat: root commit message\n")
            // Subdir has a different file that should NOT be read (we pass the repo root explicitly)
            .with_file(
                "nested/dir/.agent/commit-message.txt",
                "feat: WRONG commit message\n",
            );

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();
        run_ralph_cli_with_handler(&["--show-commit-msg"], executor, config, &mut handler).unwrap();
    });
}

/// Test that the `--show-commit-msg` flag fails when the commit message file is missing.
///
/// This verifies that when a user invokes ralph with the `--show-commit-msg` flag
/// without a commit-message.txt file, the command fails.
///
/// Note: Plumbing commands bypass config loading entirely.
#[test]
fn ralph_show_commit_msg_fails_if_missing() {
    with_default_timeout(|| {
        // Don't create commit-message.txt
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"));

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();
        let result =
            run_ralph_cli_with_handler(&["--show-commit-msg"], executor, config, &mut handler);

        // Should fail
        assert!(result.is_err());
    });
}

/// Test that the `--apply-commit` flag creates a commit with the specified message.
///
/// This verifies that when a user invokes ralph with the `--apply-commit` flag
/// and a commit-message.txt file exists, a commit effect is triggered
/// and the commit-message.txt file is cleaned up afterward.
///
/// Note: This test verifies that the commit EFFECT is triggered via the handler.
/// For tests that verify actual git2 commit creation, see `tests/system_tests/commit/`.
#[test]
fn ralph_apply_commit_creates_commit() {
    with_default_timeout(|| {
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file("new_file.txt", "content")
            .with_file(".agent/commit-message.txt", "feat: add new file")
            .with_staged_changes(true);

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();
        run_ralph_cli_with_handler(&["--apply-commit"], executor, config, &mut handler).unwrap();

        // Verify the commit effect was triggered
        let captured = handler.captured();
        assert!(
            captured
                .iter()
                .any(|e| matches!(e, AppEffect::GitCommit { .. })),
            "GitCommit effect should be triggered"
        );

        // Verify commit-message.txt was cleaned up
        assert!(
            !handler.file_exists(&PathBuf::from(".agent/commit-message.txt")),
            "commit-message.txt should be cleaned up after commit"
        );
    });
}

/// Test that the `--apply-commit` flag fails when the commit message file is missing.
///
/// This verifies that when a user invokes ralph with the `--apply-commit` flag
/// without a commit-message.txt file, the command fails.
///
/// Note: Plumbing commands bypass config loading entirely.
#[test]
fn ralph_apply_commit_fails_without_message_file() {
    with_default_timeout(|| {
        // Don't create commit-message.txt
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"));

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();
        let result =
            run_ralph_cli_with_handler(&["--apply-commit"], executor, config, &mut handler);

        // Should fail
        assert!(result.is_err());
    });
}

#[test]
fn ralph_generate_commit_msg_succeeds_without_prompt_md() {
    with_default_timeout(|| {
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_diff("diff --git a/src/lib.rs b/src/lib.rs\n+change\n")
            .with_file(
                ".agent/tmp/commit_message.xml",
                "<ralph-commit><ralph-subject>feat: generated without prompt</ralph-subject></ralph-commit>",
            );

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handler(&["--generate-commit-msg"], executor, config, &mut handler)
            .unwrap();

        assert_eq!(
            handler.get_file(&PathBuf::from(".agent/commit-message.txt")),
            Some("feat: generated without prompt".to_string())
        );
        assert!(
            !handler.file_exists(&PathBuf::from("PROMPT.md")),
            "generate-commit-msg should not require or create PROMPT.md"
        );
    });
}

#[test]
fn ralph_generate_commit_msg_keeps_generated_message_after_command_cleanup() {
    with_default_timeout(|| {
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_diff("diff --git a/src/lib.rs b/src/lib.rs\n+change\n")
            .with_file(
                ".agent/tmp/commit_message.xml",
                "<ralph-commit><ralph-subject>feat: preserved message</ralph-subject></ralph-commit>",
            );

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handler(&["--generate-commit-msg"], executor, config, &mut handler)
            .unwrap();

        assert!(
            handler.file_exists(&PathBuf::from(".agent/commit-message.txt")),
            "generated commit message should survive command cleanup"
        );
    });
}

#[test]
fn ralph_inspect_checkpoint_succeeds_without_prompt_md() {
    with_default_timeout(|| {
        let checkpoint_json = make_checkpoint_json(MOCK_REPO_PATH, "Complete", 1, 1);
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file(".agent/checkpoint.json", checkpoint_json);

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        run_ralph_cli_with_handler(&["--inspect-checkpoint"], executor, config, &mut handler)
            .unwrap();

        assert!(
            !handler.file_exists(&PathBuf::from("PROMPT.md")),
            "inspect-checkpoint should not require or create PROMPT.md"
        );
    });
}

// ============================================================================
// Note: Real Git Commit Tests Moved to System Tests
// ============================================================================
//
// Tests that verify actual git2 commit creation (checking HEAD commit message)
// have been moved to `tests/system_tests/commit/` as they require real git
// repository operations.
//
// Per INTEGRATION_TESTS.md, tests requiring real git operations belong in
// `tests/system_tests/`.
