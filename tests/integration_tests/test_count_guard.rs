//! Guard test to ensure integration test count doesn't drop unexpectedly.
//!
//! This module provides documentation and a lightweight guard to catch
//! accidental test suite regressions. The integration test count is NOT
//! currently enforced by `cargo xtask verify`; this guard test (and any CI
//! policy invoking `-- --list`) is the primary protection against drops.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** This module is part of the integration test framework and
//! MUST follow the integration test style guide defined in
//! **[../INTEGRATION_TESTS.md](../INTEGRATION_TESTS.md)**.
//!
//! # Purpose
//!
//! This test exists to:
//! - Document the expected minimum integration test count
//! - Serve as a reminder to verify the full suite is running
//! - Provide a best-effort source scan to ensure the guard stays wired up
//! - Fail loudly if the source-level count drops below the minimum floor
//!
//! # How to Verify Test Count
//!
//! Run the following command to check the actual test count:
//! ```bash
//! cargo test -p ralph-workflow-tests -- --list 2>&1 | grep -c ': test$'
//! ```
//!
//! To feed the compiled test count into this guard test without spawning
//! processes, set `RALPH_INTEGRATION_TEST_LIST_COUNT` to the `--list` count:
//! ```bash
//! RALPH_INTEGRATION_TEST_LIST_COUNT=$(cargo test -p ralph-workflow-tests -- --list 2>&1 | grep -c ': test$') \
//!   cargo test -p ralph-workflow-tests
//! ```
//!
//! The test count floor is enforced by the `integration_test_count_guard_documentation`
//! test which runs as part of `cargo test -p ralph-workflow-tests` (included in
//! `cargo xtask verify` as the test-integration check). The compliance-timeout-wrapper
//! native check in xtask/src/compliance.rs verifies timeout wrapper presence only,
//! not test count.
//!
//! NOTE: The source scan in this module is intentionally non-authoritative.
//! It only looks for literal `#[test]` annotations and does not reflect
//! conditional compilation or alternative test attributes (e.g. `#[tokio::test]`).
//! The `cargo test -- --list` output is the source of truth.

use crate::test_timeout::with_default_timeout;
use std::collections::HashSet;

/// Minimum expected integration test count.
///
/// Update this when adding new test modules. This is a floor, not an exact target.
/// The actual count should be >= this value.
///
/// If this value needs to decrease significantly, it likely indicates either:
/// - Tests were accidentally removed
/// - A test module is not being compiled
/// - The test discovery is not working correctly
pub const MINIMUM_EXPECTED_TESTS: usize = 400;

struct SourceFile {
    path: &'static str,
    contents: &'static str,
}

const INTEGRATION_TEST_SOURCES: &[SourceFile] = &[
    SourceFile {
        path: "main.rs",
        contents: include_str!("main.rs"),
    },
    SourceFile {
        path: "_TEMPLATE.rs",
        contents: include_str!("_TEMPLATE.rs"),
    },
    SourceFile {
        path: "agent_spawn_errors.rs",
        contents: include_str!("agent_spawn_errors.rs"),
    },
    SourceFile {
        path: "agent_chain_normalization.rs",
        contents: include_str!("agent_chain_normalization.rs"),
    },
    SourceFile {
        path: "awaiting_dev_fix_recovery/mod.rs",
        contents: include_str!("awaiting_dev_fix_recovery/mod.rs"),
    },
    SourceFile {
        path: "awaiting_dev_fix_recovery/basics.rs",
        contents: include_str!("awaiting_dev_fix_recovery/basics.rs"),
    },
    SourceFile {
        path: "awaiting_dev_fix_recovery/end_to_end.rs",
        contents: include_str!("awaiting_dev_fix_recovery/end_to_end.rs"),
    },
    SourceFile {
        path: "awaiting_dev_fix_recovery/orchestration.rs",
        contents: include_str!("awaiting_dev_fix_recovery/orchestration.rs"),
    },
    SourceFile {
        path: "awaiting_dev_fix_recovery/state_reset.rs",
        contents: include_str!("awaiting_dev_fix_recovery/state_reset.rs"),
    },
    SourceFile {
        path: "cli/mod.rs",
        contents: include_str!("cli/mod.rs"),
    },
    SourceFile {
        path: "cloud/mod.rs",
        contents: include_str!("cloud/mod.rs"),
    },
    SourceFile {
        path: "cloud/disabled_mode.rs",
        contents: include_str!("cloud/disabled_mode.rs"),
    },
    SourceFile {
        path: "cloud/enabled_mode.rs",
        contents: include_str!("cloud/enabled_mode.rs"),
    },
    SourceFile {
        path: "cloud/push_flow.rs",
        contents: include_str!("cloud/push_flow.rs"),
    },
    SourceFile {
        path: "behavioral_pipeline_tests.rs",
        contents: include_str!("behavioral_pipeline_tests.rs"),
    },
    SourceFile {
        path: "ccs_all_delta_types_spam_reproduction.rs",
        contents: include_str!("ccs_all_delta_types_spam_reproduction.rs"),
    },
    SourceFile {
        path: "ccs_ansi_stripping_console.rs",
        contents: include_str!("ccs_ansi_stripping_console.rs"),
    },
    SourceFile {
        path: "ccs_ansi_stripping_waterfall.rs",
        contents: include_str!("ccs_ansi_stripping_waterfall.rs"),
    },
    SourceFile {
        path: "ccs_basic_mode_nuclear_test.rs",
        contents: include_str!("ccs_basic_mode_nuclear_test.rs"),
    },
    SourceFile {
        path: "ccs_comprehensive_spam_verification.rs",
        contents: include_str!("ccs_comprehensive_spam_verification.rs"),
    },
    SourceFile {
        path: "ccs_delta_spam_systematic_reproduction.rs",
        contents: include_str!("ccs_delta_spam_systematic_reproduction.rs"),
    },
    SourceFile {
        path: "ccs_streaming_spam_all_deltas.rs",
        contents: include_str!("ccs_streaming_spam_all_deltas.rs"),
    },
    SourceFile {
        path: "ccs_extreme_streaming_regression.rs",
        contents: include_str!("ccs_extreme_streaming_regression.rs"),
    },
    SourceFile {
        path: "ccs_nuclear_full_log_regression.rs",
        contents: include_str!("ccs_nuclear_full_log_regression.rs"),
    },
    SourceFile {
        path: "ccs_nuclear_spam_test.rs",
        contents: include_str!("ccs_nuclear_spam_test.rs"),
    },
    SourceFile {
        path: "ccs_real_world_log_regression.rs",
        contents: include_str!("ccs_real_world_log_regression.rs"),
    },
    SourceFile {
        path: "ccs_streaming_edge_cases.rs",
        contents: include_str!("ccs_streaming_edge_cases.rs"),
    },
    SourceFile {
        path: "ccs_wrapping_comprehensive.rs",
        contents: include_str!("ccs_wrapping_comprehensive.rs"),
    },
    SourceFile {
        path: "ccs_wrapping_waterfall_reproduction.rs",
        contents: include_str!("ccs_wrapping_waterfall_reproduction.rs"),
    },
    SourceFile {
        path: "codex_duplicate_item_completed.rs",
        contents: include_str!("codex_duplicate_item_completed.rs"),
    },
    SourceFile {
        path: "codex_reasoning_spam_regression.rs",
        contents: include_str!("codex_reasoning_spam_regression.rs"),
    },
    SourceFile {
        path: "commit/mod.rs",
        contents: include_str!("commit/mod.rs"),
    },
    SourceFile {
        path: "commit/diff_failure_fallback.rs",
        contents: include_str!("commit/diff_failure_fallback.rs"),
    },
    SourceFile {
        path: "commit/multi_iteration_diff_freshness.rs",
        contents: include_str!("commit/multi_iteration_diff_freshness.rs"),
    },
    SourceFile {
        path: "commit/pre_termination_safety.rs",
        contents: include_str!("commit/pre_termination_safety.rs"),
    },
    SourceFile {
        path: "commit_xml_validation.rs",
        contents: include_str!("commit_xml_validation.rs"),
    },
    SourceFile {
        path: "common/mod.rs",
        contents: include_str!("common/mod.rs"),
    },
    SourceFile {
        path: "deduplication/mod.rs",
        contents: include_str!("deduplication/mod.rs"),
    },
    SourceFile {
        path: "deduplication/streaming.rs",
        contents: include_str!("deduplication/streaming.rs"),
    },
    SourceFile {
        path: "deduplication/assistant_events.rs",
        contents: include_str!("deduplication/assistant_events.rs"),
    },
    SourceFile {
        path: "deduplication/edge_cases.rs",
        contents: include_str!("deduplication/edge_cases.rs"),
    },
    SourceFile {
        path: "dylint_target.rs",
        contents: include_str!("dylint_target.rs"),
    },
    SourceFile {
        path: "development_xml_validation.rs",
        contents: include_str!("development_xml_validation.rs"),
    },
    SourceFile {
        path: "event_loop_trace_dump.rs",
        contents: include_str!("event_loop_trace_dump.rs"),
    },
    SourceFile {
        path: "fix_xml_validation.rs",
        contents: include_str!("fix_xml_validation.rs"),
    },
    SourceFile {
        path: "gemini_parser_tests.rs",
        contents: include_str!("gemini_parser_tests.rs"),
    },
    SourceFile {
        path: "gitignore_enforcement.rs",
        contents: include_str!("gitignore_enforcement.rs"),
    },
    SourceFile {
        path: "git/mod.rs",
        contents: include_str!("git/mod.rs"),
    },
    SourceFile {
        path: "logger/mod.rs",
        contents: include_str!("logger/mod.rs"),
    },
    SourceFile {
        path: "logger/test_logger_tests.rs",
        contents: include_str!("logger/test_logger_tests.rs"),
    },
    SourceFile {
        path: "logging_per_run.rs",
        contents: include_str!("logging_per_run.rs"),
    },
    SourceFile {
        path: "loop_detection_after_additional_events.rs",
        contents: include_str!("loop_detection_after_additional_events.rs"),
    },
    SourceFile {
        path: "loop_detection_recovery.rs",
        contents: include_str!("loop_detection_recovery.rs"),
    },
    SourceFile {
        path: "memory_safety/mod.rs",
        contents: include_str!("memory_safety/mod.rs"),
    },
    SourceFile {
        path: "memory_safety/bounded_growth.rs",
        contents: include_str!("memory_safety/bounded_growth.rs"),
    },
    SourceFile {
        path: "memory_safety/thread_lifecycle.rs",
        contents: include_str!("memory_safety/thread_lifecycle.rs"),
    },
    SourceFile {
        path: "memory_safety/channel_bounds.rs",
        contents: include_str!("memory_safety/channel_bounds.rs"),
    },
    SourceFile {
        path: "memory_safety/long_running_pipeline.rs",
        contents: include_str!("memory_safety/long_running_pipeline.rs"),
    },
    SourceFile {
        path: "memory_safety/unsafe_patterns.rs",
        contents: include_str!("memory_safety/unsafe_patterns.rs"),
    },
    SourceFile {
        path: "memory_safety/edge_cases.rs",
        contents: include_str!("memory_safety/edge_cases.rs"),
    },
    SourceFile {
        path: "opencode_parser_tests.rs",
        contents: include_str!("opencode_parser_tests.rs"),
    },
    SourceFile {
        path: "opencode_usage_limit_detection.rs",
        contents: include_str!("opencode_usage_limit_detection.rs"),
    },
    SourceFile {
        path: "offline_detection.rs",
        contents: include_str!("offline_detection.rs"),
    },
    SourceFile {
        path: "prompt_path_resolution.rs",
        contents: include_str!("prompt_path_resolution.rs"),
    },
    SourceFile {
        path: "prompt_permissions.rs",
        contents: include_str!("prompt_permissions.rs"),
    },
    SourceFile {
        path: "plan_xml_validation.rs",
        contents: include_str!("plan_xml_validation.rs"),
    },
    SourceFile {
        path: "reducer_agent_fallback.rs",
        contents: include_str!("reducer_agent_fallback.rs"),
    },
    SourceFile {
        path: "reducer_effect_invariants.rs",
        contents: include_str!("reducer_effect_invariants.rs"),
    },
    SourceFile {
        path: "reducer_error_handling.rs",
        contents: include_str!("reducer_error_handling.rs"),
    },
    SourceFile {
        path: "reducer_fault_tolerance/mod.rs",
        contents: include_str!("reducer_fault_tolerance/mod.rs"),
    },
    SourceFile {
        path: "reducer_fault_tolerance/helpers.rs",
        contents: include_str!("reducer_fault_tolerance/helpers.rs"),
    },
    SourceFile {
        path: "reducer_fault_tolerance/agent_crash_handling.rs",
        contents: include_str!("reducer_fault_tolerance/agent_crash_handling.rs"),
    },
    SourceFile {
        path: "reducer_fault_tolerance/model_fallback.rs",
        contents: include_str!("reducer_fault_tolerance/model_fallback.rs"),
    },
    SourceFile {
        path: "reducer_fault_tolerance/continuation_exhaustion.rs",
        contents: include_str!("reducer_fault_tolerance/continuation_exhaustion.rs"),
    },
    SourceFile {
        path: "reducer_fault_tolerance/failure_completion_marker/mod.rs",
        contents: include_str!("reducer_fault_tolerance/failure_completion_marker/mod.rs"),
    },
    SourceFile {
        path: "reducer_fault_tolerance/failure_completion_marker/common.rs",
        contents: include_str!("reducer_fault_tolerance/failure_completion_marker/common.rs"),
    },
    SourceFile {
        path: "reducer_fault_tolerance/failure_completion_marker/state_transitions.rs",
        contents: include_str!(
            "reducer_fault_tolerance/failure_completion_marker/state_transitions.rs"
        ),
    },
    SourceFile {
        path: "reducer_fault_tolerance/failure_completion_marker/marker_emission.rs",
        contents: include_str!(
            "reducer_fault_tolerance/failure_completion_marker/marker_emission.rs"
        ),
    },
    SourceFile {
        path: "reducer_fault_tolerance/failure_completion_marker/error_handling.rs",
        contents: include_str!(
            "reducer_fault_tolerance/failure_completion_marker/error_handling.rs"
        ),
    },
    SourceFile {
        path: "reducer_hidden_behavior.rs",
        contents: include_str!("reducer_hidden_behavior.rs"),
    },
    SourceFile {
        path: "reducer_rebase_state_machine.rs",
        contents: include_str!("reducer_rebase_state_machine.rs"),
    },
    SourceFile {
        path: "reducer_resume.rs",
        contents: include_str!("reducer_resume.rs"),
    },
    SourceFile {
        path: "reducer_resume_tests.rs",
        contents: include_str!("reducer_resume_tests.rs"),
    },
    SourceFile {
        path: "reducer_resume_boundary_tests.rs",
        contents: include_str!("reducer_resume_boundary_tests.rs"),
    },
    SourceFile {
        path: "reducer_legacy_rejection/mod.rs",
        contents: include_str!("reducer_legacy_rejection/mod.rs"),
    },
    SourceFile {
        path: "reducer_legacy_rejection/checkpoint_format.rs",
        contents: include_str!("reducer_legacy_rejection/checkpoint_format.rs"),
    },
    SourceFile {
        path: "reducer_legacy_rejection/legacy_phase_rejection.rs",
        contents: include_str!("reducer_legacy_rejection/legacy_phase_rejection.rs"),
    },
    SourceFile {
        path: "reducer_legacy_rejection/reducer_purity_invariants/mod.rs",
        contents: include_str!("reducer_legacy_rejection/reducer_purity_invariants/mod.rs"),
    },
    SourceFile {
        path: "reducer_legacy_rejection/reducer_purity_invariants/control_flow.rs",
        contents: include_str!(
            "reducer_legacy_rejection/reducer_purity_invariants/control_flow.rs"
        ),
    },
    SourceFile {
        path: "reducer_legacy_rejection/reducer_purity_invariants/effects_and_phases.rs",
        contents: include_str!(
            "reducer_legacy_rejection/reducer_purity_invariants/effects_and_phases.rs"
        ),
    },
    SourceFile {
        path: "reducer_legacy_rejection/xsd_retry_invariants.rs",
        contents: include_str!("reducer_legacy_rejection/xsd_retry_invariants.rs"),
    },
    SourceFile {
        path: "reducer_legacy_rejection/archival_invariants.rs",
        contents: include_str!("reducer_legacy_rejection/archival_invariants.rs"),
    },
    SourceFile {
        path: "reducer_state_machine.rs",
        contents: include_str!("reducer_state_machine.rs"),
    },
    SourceFile {
        path: "required_files_cleanup.rs",
        contents: include_str!("required_files_cleanup.rs"),
    },
    SourceFile {
        path: "review_output_validation.rs",
        contents: include_str!("review_output_validation.rs"),
    },
    SourceFile {
        path: "review_xml_validation.rs",
        contents: include_str!("review_xml_validation.rs"),
    },
    SourceFile {
        path: "rust_lsp_dylint.rs",
        contents: include_str!("rust_lsp_dylint.rs"),
    },
    SourceFile {
        path: "test_count_guard.rs",
        contents: include_str!("test_count_guard.rs"),
    },
    SourceFile {
        path: "test_timeout.rs",
        contents: include_str!("test_timeout.rs"),
    },
    SourceFile {
        path: "test_traits.rs",
        contents: include_str!("test_traits.rs"),
    },
    SourceFile {
        path: "timeout_file_activity.rs",
        contents: include_str!("timeout_file_activity.rs"),
    },
    SourceFile {
        path: "template_rendering_errors.rs",
        contents: include_str!("template_rendering_errors.rs"),
    },
    SourceFile {
        path: "template_validation_jsx.rs",
        contents: include_str!("template_validation_jsx.rs"),
    },
    SourceFile {
        path: "ui_events.rs",
        contents: include_str!("ui_events.rs"),
    },
    SourceFile {
        path: "workflows/mod.rs",
        contents: include_str!("workflows/mod.rs"),
    },
    SourceFile {
        path: "workflows/analysis.rs",
        contents: include_str!("workflows/analysis.rs"),
    },
    SourceFile {
        path: "workflows/backup.rs",
        contents: include_str!("workflows/backup.rs"),
    },
    SourceFile {
        path: "workflows/baseline.rs",
        contents: include_str!("workflows/baseline.rs"),
    },
    SourceFile {
        path: "workflows/cleanup.rs",
        contents: include_str!("workflows/cleanup.rs"),
    },
    SourceFile {
        path: "workflows/commit_tests.rs",
        contents: include_str!("workflows/commit_tests.rs"),
    },
    SourceFile {
        path: "workflows/commit_residuals.rs",
        contents: include_str!("workflows/commit_residuals.rs"),
    },
    SourceFile {
        path: "workflows/config/mod.rs",
        contents: include_str!("workflows/config/mod.rs"),
    },
    SourceFile {
        path: "workflows/config/init_and_defaults.rs",
        contents: include_str!("workflows/config/init_and_defaults.rs"),
    },
    SourceFile {
        path: "workflows/config/local_and_validation.rs",
        contents: include_str!("workflows/config/local_and_validation.rs"),
    },
    SourceFile {
        path: "workflows/config/modes_and_review.rs",
        contents: include_str!("workflows/config/modes_and_review.rs"),
    },
    SourceFile {
        path: "workflows/config/worktree_and_merge.rs",
        contents: include_str!("workflows/config/worktree_and_merge.rs"),
    },
    SourceFile {
        path: "workflows/config_test.rs",
        contents: include_str!("workflows/config_test.rs"),
    },
    SourceFile {
        path: "workflows/continuation.rs",
        contents: include_str!("workflows/continuation.rs"),
    },
    SourceFile {
        path: "workflows/continuation_budget/mod.rs",
        contents: include_str!("workflows/continuation_budget/mod.rs"),
    },
    SourceFile {
        path: "workflows/continuation_budget/core_enforcement.rs",
        contents: include_str!("workflows/continuation_budget/core_enforcement.rs"),
    },
    SourceFile {
        path: "workflows/continuation_budget/defaults_and_boundaries.rs",
        contents: include_str!("workflows/continuation_budget/defaults_and_boundaries.rs"),
    },
    SourceFile {
        path: "workflows/continuation_budget/regression_and_guards.rs",
        contents: include_str!("workflows/continuation_budget/regression_and_guards.rs"),
    },
    SourceFile {
        path: "workflows/development_xml.rs",
        contents: include_str!("workflows/development_xml.rs"),
    },
    SourceFile {
        path: "workflows/fallback.rs",
        contents: include_str!("workflows/fallback.rs"),
    },
    SourceFile {
        path: "workflows/iteration_counter.rs",
        contents: include_str!("workflows/iteration_counter.rs"),
    },
    SourceFile {
        path: "workflows/no_premature_exit.rs",
        contents: include_str!("workflows/no_premature_exit.rs"),
    },
    SourceFile {
        path: "workflows/oversize_prompt.rs",
        contents: include_str!("workflows/oversize_prompt.rs"),
    },
    SourceFile {
        path: "workflows/plan.rs",
        contents: include_str!("workflows/plan.rs"),
    },
    SourceFile {
        path: "workflows/review.rs",
        contents: include_str!("workflows/review.rs"),
    },
    SourceFile {
        path: "workflows/summary_consistency.rs",
        contents: include_str!("workflows/summary_consistency.rs"),
    },
    SourceFile {
        path: "workflows/resume/mod.rs",
        contents: include_str!("workflows/resume/mod.rs"),
    },
    SourceFile {
        path: "workflows/resume/basic.rs",
        contents: include_str!("workflows/resume/basic.rs"),
    },
    SourceFile {
        path: "workflows/resume/checkpoint.rs",
        contents: include_str!("workflows/resume/checkpoint.rs"),
    },
    SourceFile {
        path: "workflows/resume/phases.rs",
        contents: include_str!("workflows/resume/phases.rs"),
    },
    SourceFile {
        path: "workflows/resume/preservation.rs",
        contents: include_str!("workflows/resume/preservation.rs"),
    },
    SourceFile {
        path: "workflows/resume/rebase.rs",
        contents: include_str!("workflows/resume/rebase.rs"),
    },
    SourceFile {
        path: "workflows/resume/v3/mod.rs",
        contents: include_str!("workflows/resume/v3/mod.rs"),
    },
    SourceFile {
        path: "workflows/resume/v3/execution_history.rs",
        contents: include_str!("workflows/resume/v3/execution_history.rs"),
    },
    SourceFile {
        path: "workflows/resume/v3/file_system_state.rs",
        contents: include_str!("workflows/resume/v3/file_system_state.rs"),
    },
    SourceFile {
        path: "workflows/resume/v3/prompt_history.rs",
        contents: include_str!("workflows/resume/v3/prompt_history.rs"),
    },
    SourceFile {
        path: "workflows/resume/v3/rebase_conflict_replay.rs",
        contents: include_str!("workflows/resume/v3/rebase_conflict_replay.rs"),
    },
    SourceFile {
        path: "workflows/resume/v3/smoke.rs",
        contents: include_str!("workflows/resume/v3/smoke.rs"),
    },
    SourceFile {
        path: "xsd_retry_missing_files.rs",
        contents: include_str!("xsd_retry_missing_files.rs"),
    },
    SourceFile {
        path: "xsd_retry_workflow.rs",
        contents: include_str!("xsd_retry_workflow.rs"),
    },
];

fn source_for_path(path: &str) -> Option<&'static str> {
    INTEGRATION_TEST_SOURCES
        .iter()
        .find(|source| source.path == path)
        .map(|source| source.contents)
}

fn count_test_annotations(contents: &str) -> usize {
    contents
        .lines()
        .filter(|line| {
            let trimmed = line.trim_start();
            trimmed.starts_with("#[test]")
        })
        .count()
}

fn parse_module_declarations(contents: &str) -> Vec<&str> {
    contents
        .lines()
        .filter_map(|line| {
            let trimmed = line.trim_start();
            if trimmed.starts_with("//") || !trimmed.contains(';') {
                return None;
            }
            let declaration = if let Some(rest) = trimmed.strip_prefix("mod ") {
                rest
            } else if let Some(rest) = trimmed.strip_prefix("pub mod ") {
                rest
            } else {
                return None;
            };
            let name = declaration
                .split(|ch: char| ch == ';' || ch.is_whitespace())
                .next()
                .unwrap_or("");
            if name.is_empty() {
                None
            } else {
                Some(name)
            }
        })
        .collect()
}

fn resolve_module_path(module_dir: &str, module_name: &str) -> String {
    let base = if module_dir.is_empty() {
        module_name.to_string()
    } else {
        format!("{module_dir}/{module_name}")
    };
    let candidate_file = format!("{base}.rs");
    if source_for_path(&candidate_file).is_some() {
        return candidate_file;
    }
    let candidate_mod = format!("{base}/mod.rs");
    if source_for_path(&candidate_mod).is_some() {
        return candidate_mod;
    }
    panic!(
        "Missing integration test module source for '{base}'. Tried '{candidate_file}' and '{candidate_mod}'"
    );
}

fn module_dir_from_path(path: &str) -> String {
    path.strip_suffix("/mod.rs").map_or_else(
        || {
            path.rsplit_once('/')
                .map_or_else(String::new, |(dir, _)| dir.to_string())
        },
        std::string::ToString::to_string,
    )
}

#[test]
fn integration_test_sources_have_unique_paths() {
    with_default_timeout(|| {
        let mut seen = HashSet::new();
        let mut duplicates = Vec::new();

        for source in INTEGRATION_TEST_SOURCES {
            if !seen.insert(source.path) {
                duplicates.push(source.path);
            }
        }

        duplicates.sort_unstable();
        duplicates.dedup();

        assert!(
            duplicates.is_empty(),
            "INTEGRATION_TEST_SOURCES contains duplicate paths: {duplicates:?}"
        );
    });
}

/// Best-effort source scan of literal `#[test]` annotations.
///
/// This is not an authoritative measure of discovered tests; it exists only to
/// ensure the guard module stays connected to the integration test source tree.
fn count_tests_from_module_tree() -> usize {
    let mut visited = HashSet::new();
    let main_contents = source_for_path("main.rs")
        .expect("tests/integration_tests/main.rs must be included in guard sources");
    count_tests_recursive("main.rs", "", main_contents, &mut visited)
}

fn compiled_test_count_from_env() -> Option<usize> {
    let value = std::env::var("RALPH_INTEGRATION_TEST_LIST_COUNT").ok()?;
    value.trim().parse::<usize>().ok()
}

fn count_tests_recursive(
    file_path: &str,
    module_dir: &str,
    contents: &str,
    visited: &mut HashSet<String>,
) -> usize {
    if !visited.insert(file_path.to_string()) {
        return 0;
    }

    let mut total = count_test_annotations(contents);
    for module_name in parse_module_declarations(contents) {
        let child_path = resolve_module_path(module_dir, module_name);
        let child_contents = source_for_path(&child_path)
            .unwrap_or_else(|| panic!("Missing integration test source '{child_path}'"));
        let child_dir = module_dir_from_path(&child_path);
        total += count_tests_recursive(&child_path, &child_dir, child_contents, visited);
    }
    total
}

/// This test documents and enforces the expected minimum test count.
///
/// This verifies that the test count guard module is properly loaded, the
/// constant is accessible, and the best-effort source scan has not regressed
/// below the minimum floor. The authoritative compiled test list verification
/// happens when a caller runs `cargo test -p ralph-workflow-tests -- --list`.
/// `cargo xtask verify` currently runs the integration test suite, but does not
/// invoke `-- --list` or enforce a compiled test count directly.
///
/// If this test appears, it means the test count guard module is properly loaded
/// and the integration test suite includes this verification documentation.
#[test]
fn integration_test_count_guard_documentation() {
    with_default_timeout(|| {
        let min = MINIMUM_EXPECTED_TESTS;
        assert!(min > 0, "MINIMUM_EXPECTED_TESTS should be positive");

        let actual_count = count_tests_from_module_tree();
        assert!(
            actual_count >= MINIMUM_EXPECTED_TESTS,
            "Source scan found {actual_count} #[test] annotations; expected at least {MINIMUM_EXPECTED_TESTS}"
        );

        if let Some(compiled_count) = compiled_test_count_from_env() {
            assert!(
                compiled_count >= MINIMUM_EXPECTED_TESTS,
                "Compiled test list count {compiled_count} is below MINIMUM_EXPECTED_TESTS ({MINIMUM_EXPECTED_TESTS}). \
Ensure you're running `cargo test -p ralph-workflow-tests`."
            );
        }
    });
}
