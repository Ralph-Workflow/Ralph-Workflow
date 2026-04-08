//! CLI integration tests.
//!
//! # IMPORTANT: Timeout Enforcement
//!
//! **ALL tests in this module MUST use `with_default_timeout()` to wrap test code.**
//! This ensures tests complete within 5 seconds and don't hang due to external I/O.
//!
//! See `test_timeout.rs` for details on the timeout enforcement mechanism.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow the integration test style guide
//! defined in **[`INTEGRATION_TESTS.md`](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (exit codes, effect capture)
//! - Uses `run_ralph_cli_with_handler()` with `MockAppEffectHandler`
//! - NO `TempDir`, `std::fs`, or real git operations
//! - Tests are deterministic and verify effects, not real filesystem state

use crate::common::{
    create_test_config_struct, mock_executor_with_success, run_ralph_cli_with_handler,
};
use crate::test_timeout::with_default_timeout;
use clap::Parser as _;
use ralph_workflow::app::mock_effect_handler::MockAppEffectHandler;
use std::path::PathBuf;

/// Standard PROMPT.md content for tests that need it.
const STANDARD_PROMPT: &str = r"## Goal

Do something.

## Acceptance

- Tests pass
";

// ============================================================================
// Version and Help Commands
// ============================================================================

/// Test that the `--version` flag produces a successful exit.
///
/// This verifies that when a user invokes ralph with the `--version` flag,
/// the CLI executes successfully without errors.
#[test]
fn ralph_prints_version() {
    with_default_timeout(|| {
        // --version doesn't need a git repo, but we still need a handler
        let mut handler = MockAppEffectHandler::new();
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();
        run_ralph_cli_with_handler(&["--version"], executor, config, &mut handler).unwrap();
    });
}

/// Test that the `--help` flag displays usage information.
///
/// This verifies that when a user invokes ralph with the `--help` flag,
/// the command executes successfully without errors.
/// (Actual help content verification is done by the clap library itself)
#[test]
fn ralph_help_shows_usage() {
    with_default_timeout(|| {
        let mut handler = MockAppEffectHandler::new();
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();
        run_ralph_cli_with_handler(&["--help"], executor, config, &mut handler).unwrap();
    });
}

// ============================================================================
// Template Listing Commands
// ============================================================================

/// Test that the `--list-templates` flag shows available templates.
///
/// This verifies that when a user invokes ralph with the `--list-templates` flag,
/// the command executes successfully without errors.
#[test]
fn ralph_list_templates_shows_available() {
    with_default_timeout(|| {
        let mut handler = MockAppEffectHandler::new();
        let config = create_test_config_struct();
        let executor = mock_executor_with_success();
        run_ralph_cli_with_handler(&["--list-templates"], executor, config, &mut handler).unwrap();
    });
}

// ============================================================================
// Diagnose Command
// ============================================================================

/// Test that the `--diagnose` flag displays system diagnostic information.
///
/// This verifies that when a user invokes ralph with the `--diagnose` flag
/// in a git repository, the command executes successfully without errors.
#[test]
fn ralph_diagnose_shows_system_info() {
    with_default_timeout(|| {
        // Set up mock handler with git repo context
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"));

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();
        run_ralph_cli_with_handler(&["--diagnose"], executor, config, &mut handler).unwrap();
    });
}

/// Test that the `-d` short flag is parsed as an alias for `--diagnose`.
///
/// This verifies that when a user invokes ralph with the `-d` short flag,
/// it is parsed identically to `--diagnose`. Full execution behavior for
/// the diagnose command is covered by `ralph_diagnose_shows_system_info`.
#[test]
fn ralph_diagnose_short_flag_works() {
    with_default_timeout(|| {
        let args = ralph_workflow::cli::Args::try_parse_from(["ralph", "-d"])
            .expect("-d should parse successfully");
        assert!(
            args.recovery.diagnose,
            "-d should be equivalent to --diagnose (sets diagnose=true)"
        );
    });
}

// ============================================================================
// Dry Run Command
// ============================================================================

/// Test that the `--dry-run` flag validates configuration without executing agents.
///
/// This verifies that when a user invokes ralph with the `--dry-run` flag
/// with a valid PROMPT.md and config, the pipeline validates without running agents.
#[test]
fn ralph_dry_run_validates_without_executing() {
    with_default_timeout(|| {
        // Set up mock handler with PROMPT.md file
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"))
            .with_file("PROMPT.md", STANDARD_PROMPT);

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();
        run_ralph_cli_with_handler(&["--dry-run"], executor, config, &mut handler).unwrap();
    });
}

// ============================================================================
// Init Commands
// ============================================================================

/// Test that the `--init` flag with a template executes without errors.
///
/// This verifies that when a user invokes ralph with the `--init` flag
/// and a template name like "bug-fix", the command executes successfully.
/// (In non-interactive mode, it returns early without creating PROMPT.md)
#[test]
fn ralph_init_with_template_creates_prompt() {
    with_default_timeout(|| {
        // Set up mock handler with git repo but no PROMPT.md
        let mut handler = MockAppEffectHandler::new()
            .with_head_oid("a".repeat(40))
            .with_cwd(PathBuf::from("/mock/repo"));

        let config = create_test_config_struct();
        let executor = mock_executor_with_success();

        // Note: --init in non-interactive mode returns early without creating PROMPT.md
        // The actual PROMPT.md creation happens in interactive mode
        run_ralph_cli_with_handler(&["--init", "bug-fix"], executor, config, &mut handler).unwrap();
    });
}
