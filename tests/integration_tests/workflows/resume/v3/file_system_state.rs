//! Integration tests for v3 checkpoint file system state validation.
//!
//! Verifies that when resuming from v3 checkpoints, the file system state
//! (checksums, sizes) is validated to prevent resume with stale data.

use std::path::PathBuf;

use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;

use crate::common::{
    create_test_config_struct, mock_executor_with_success, run_ralph_cli_with_handler,
};
use crate::test_timeout::{with_default_timeout, with_timeout_ctx};

use super::super::{
    make_checkpoint_with_file_system_state, MOCK_REPO_PATH, STANDARD_PROMPT,
    STANDARD_PROMPT_CHECKSUM,
};

// ============================================================================
// V3 Hardened Resume Tests - File System State
// ============================================================================

#[test]
fn ralph_v3_file_system_state_validates_on_resume() {
    with_default_timeout(|| {
        // Use STANDARD_PROMPT which matches STANDARD_PROMPT_CHECKSUM in the checkpoint
        // Create file system state JSON with the matching checksum
        let file_system_state_json = format!(
            r#"{{
            "files": {{
                "PROMPT.md": {{
                    "path": "PROMPT.md",
                    "checksum": "{}",
                    "size": {},
                    "content": null,
                    "exists": true
                }}
            }},
            "git_head_oid": null,
            "git_branch": null
        }}"#,
            STANDARD_PROMPT_CHECKSUM,
            STANDARD_PROMPT.len()
        );

        let checkpoint_json = make_checkpoint_with_file_system_state(
            MOCK_REPO_PATH,
            "Development",
            &file_system_state_json,
        );

        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file(".agent/checkpoint.json", &checkpoint_json);

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Resume - should validate file system state successfully
        run_ralph_cli_with_handler(&["--resume"], executor, config, &mut handler).unwrap();
    });
}

#[test]
fn ralph_v3_file_system_state_detects_changes() {
    with_default_timeout(|| {
        // The checkpoint uses STANDARD_PROMPT_CHECKSUM, but we put a different checksum
        // in file_system_state to simulate that the file has changed.
        let fake_old_checksum = "0000000000000000000000000000000000000000000000000000000000000000";

        // Create file system state JSON with fake old checksum (simulates file has changed)
        let file_system_state_json = format!(
            r#"{{
            "files": {{
                "PROMPT.md": {{
                    "path": "PROMPT.md",
                    "checksum": "{fake_old_checksum}",
                    "size": 100,
                    "content": null,
                    "exists": true
                }}
            }},
            "git_head_oid": null,
            "git_branch": null
        }}"#
        );

        let checkpoint_json = make_checkpoint_with_file_system_state(
            MOCK_REPO_PATH,
            "Development",
            &file_system_state_json,
        );

        // Create handler with STANDARD_PROMPT (different from file_system_state checksum)
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file(".agent/checkpoint.json", &checkpoint_json);

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Resume with --recovery-strategy=fail should detect the change and error
        let result = run_ralph_cli_with_handler(
            &["--resume", "--recovery-strategy", "fail"],
            executor,
            config,
            &mut handler,
        );

        // With strategy=fail, file system state mismatch should cause an error
        assert!(
            result.is_err(),
            "Should fail when file system state has changed and strategy is 'fail'"
        );
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("File system state")
                || err.contains("validation failed")
                || err.contains("changed"),
            "Error should mention file system state change: {err}"
        );
    });
}

#[test]
fn ralph_v3_file_system_state_auto_recovery() {
    use ralph_workflow::reducer::prompt_inputs::sha256_hex_str;
    with_timeout_ctx(
        |_ctx| {
            // Small PLAN.md content
            let plan_content = "Small plan content";

            // Calculate checksum
            let checksum = sha256_hex_str(plan_content);

            // Create file system state JSON with content for recovery
            let file_system_state_json = format!(
                r#"{{
            "files": {{
                ".agent/PLAN.md": {{
                    "path": ".agent/PLAN.md",
                    "checksum": "{}",
                    "size": {},
                    "content": "{}",
                    "exists": true
                }}
            }},
            "git_head_oid": null,
            "git_branch": null
        }}"#,
                checksum,
                plan_content.len(),
                plan_content
            );

            let checkpoint_json = make_checkpoint_with_file_system_state(
                MOCK_REPO_PATH,
                "Development",
                &file_system_state_json,
            );

            // Create handler with MODIFIED PLAN.md
            let mut handler = MockAppEffectHandler::new()
                .with_head_oid("a".repeat(40))
                .with_cwd(PathBuf::from(MOCK_REPO_PATH))
                .with_file("PROMPT.md", STANDARD_PROMPT)
                .with_file(".agent/PLAN.md", "Modified plan content")
                .with_file(".agent/checkpoint.json", &checkpoint_json);

            let config = create_test_config_struct();
            let executor = mock_executor_with_success();

            // Resume with --recovery-strategy=auto should restore the file from checkpoint state.
            // If auto-recovery fails, run_ralph_cli_with_handler returns an error.
            run_ralph_cli_with_handler(
                &["--resume", "--recovery-strategy", "auto"],
                executor,
                config,
                &mut handler,
            )
            .unwrap();

            // After successful pipeline completion, generated files (.agent/PLAN.md)
            // are cleaned up by finalize_pipeline. The pipeline completing without
            // error proves auto-recovery restored PLAN.md from checkpoint content
            // (the checkpoint had correct content, and the handler started with
            // modified content that would fail validation without recovery).
            // Verify cleanup happened correctly:
            assert!(
                handler.get_file(&PathBuf::from(".agent/PLAN.md")).is_none(),
                "PLAN.md should be cleaned up after pipeline completion"
            );
        },
        // Pipeline runner + event loop can be slower under CI load
        std::time::Duration::from_secs(10),
    );
}

// ============================================================================
// Hardened Resume Tests (V3 Checkpoint)
// ============================================================================

#[test]
fn ralph_v3_checkpoint_contains_file_system_state() {
    with_default_timeout(|| {
        // Use STANDARD_PROMPT which matches STANDARD_PROMPT_CHECKSUM in the checkpoint
        // Create file system state JSON with matching checksum
        let file_system_state_json = format!(
            r#"{{
            "files": {{
                "PROMPT.md": {{
                    "path": "PROMPT.md",
                    "checksum": "{}",
                    "size": {},
                    "content": null,
                    "exists": true
                }}
            }},
            "git_head_oid": null,
            "git_branch": null
        }}"#,
            STANDARD_PROMPT_CHECKSUM,
            STANDARD_PROMPT.len()
        );

        let checkpoint_json = make_checkpoint_with_file_system_state(
            MOCK_REPO_PATH,
            "Complete",
            &file_system_state_json,
        );

        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file("PROMPT.md", STANDARD_PROMPT)
            .with_file(".agent/checkpoint.json", &checkpoint_json)
            .with_file(".agent/PLAN.md", "Test plan\n")
            .with_file(".agent/commit-message.txt", "feat: test\n");

        // Verify checkpoint has file_system_state
        assert!(
            checkpoint_json.contains("\"file_system_state\""),
            "V3 checkpoint should contain file_system_state"
        );
        assert!(
            checkpoint_json.contains("PROMPT.md"),
            "File system state should capture PROMPT.md"
        );

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Verify checkpoint can be loaded and resumed
        run_ralph_cli_with_handler(&["--resume"], executor, config, &mut handler).unwrap();
    });
}

#[test]
fn ralph_resume_with_force_strategy_ignores_file_changes() {
    with_default_timeout(|| {
        // Create file system state with wrong checksum
        let file_system_state_json = r#"{
            "files": {
                "PROMPT.md": {
                    "path": "PROMPT.md",
                    "checksum": "wrongchecksum",
                    "size": 100,
                    "content": null,
                    "exists": true
                }
            },
            "git_head_oid": null,
            "git_branch": null
        }"#;

        let checkpoint_json = make_checkpoint_with_file_system_state(
            MOCK_REPO_PATH,
            "Complete",
            file_system_state_json,
        );

        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file("PROMPT.md", "# Test\nOriginal.")
            .with_file(".agent/checkpoint.json", &checkpoint_json)
            .with_file(".agent/PLAN.md", "Test plan\n")
            .with_file(".agent/commit-message.txt", "feat: test\n");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Run with --resume --recovery-strategy=force
        run_ralph_cli_with_handler(
            &["--resume", "--recovery-strategy=force"],
            executor,
            config,
            &mut handler,
        )
        .unwrap();
    });
}

#[test]
fn ralph_resume_auto_strategy_attempts_recovery() {
    with_default_timeout(|| {
        // Small PLAN.md file content
        let plan_content = "Small plan";

        // Create file system state with content for recovery
        let file_system_state_json = format!(
            r#"{{
            "files": {{
                ".agent/PLAN.md": {{
                    "path": ".agent/PLAN.md",
                    "checksum": "abc123",
                    "size": {},
                    "content": "{}",
                    "exists": true
                }}
            }},
            "git_head_oid": null,
            "git_branch": null
        }}"#,
            plan_content.len(),
            plan_content
        );

        let checkpoint_json = make_checkpoint_with_file_system_state(
            MOCK_REPO_PATH,
            "Complete",
            &file_system_state_json,
        );

        // Don't create PLAN.md - test should recover it
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from(MOCK_REPO_PATH))
            .with_file(".agent/checkpoint.json", &checkpoint_json)
            .with_file(".agent/commit-message.txt", "feat: test\n");

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Run with --resume --recovery-strategy=auto
        run_ralph_cli_with_handler(
            &["--resume", "--recovery-strategy=auto"],
            executor,
            config,
            &mut handler,
        )
        .unwrap();
    });
}
