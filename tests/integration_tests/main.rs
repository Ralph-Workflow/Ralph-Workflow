// Lint policy: integration tests still follow the repository style guide.
//
// See `CODE_STYLE.md`, `docs/code-style/testing.md`,
// `docs/code-style/boundaries.md`, and `tests/clippy.toml`.
//
// Keep test-only exceptions narrow: test code may differ from production code, but
// it should still assert behavior, avoid debug leftovers, and preserve explicit
// architectural boundaries.
#![deny(warnings)]
#![deny(clippy::all)]
#![forbid(unsafe_code)]
#![deny(
    // No explicit iterator loops when a more idiomatic form exists
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    // Test binaries keep the shared baseline and allow only the output patterns that
    // are intrinsic to test diagnostics.
    clippy::dbg_macro,
    // Push toward combinators instead of hand-written control flow
    clippy::manual_map,
    clippy::manual_filter,
    clippy::manual_find,
    clippy::manual_filter_map,
    clippy::manual_flatten,
    clippy::needless_collect
)]
//! Integration tests for ralph-workflow
//!
//! This is the main entry point for all integration tests.
//! Each module is declared here as a submodule.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All integration tests MUST follow the style guide defined in
//! **[`INTEGRATION_TESTS.md`](../INTEGRATION_TESTS.md)**.
//!
//! Before writing, modifying, or debugging any integration test, you MUST read
//! that document. It defines non-negotiable rules for:
//!
//! - **Behavior-based testing:** Test observable behavior, not implementation
//! - **Mocking strategy:** Mock only at architectural boundaries (filesystem, network)
//! - **When to update tests:** Only update when expected behavior changes
//! - **Forbidden patterns:** No `cfg!(test)` branches in production code
//! - **No process spawning:** Tests must NOT spawn external processes
//!
//! Key patterns used in these tests:
//! - **Parser tests:** Use `TestPrinter` from `ralph_workflow::json_parser::printer`
//! - **File operations:** Use `MemoryWorkspace` for isolation (NOT `TempDir`)
//! - **CLI tests:** Use `run_ralph_cli_injected()` which calls `app::run_with_config()` directly
//! - **Process execution:** Use `MockProcessExecutor` (never spawn real processes in tests)
//!
//! Tests requiring real git/filesystem operations are in `tests/system_tests/`.
//! See `tests/system_tests/SYSTEM_TESTS.md` for those guidelines.

mod agent_chain_normalization;
mod agent_spawn_errors;
mod awaiting_dev_fix_recovery;
mod behavioral_pipeline_tests;
mod ccs_all_delta_types_spam_reproduction;
mod ccs_ansi_stripping_console;
mod ccs_ansi_stripping_waterfall;
mod ccs_basic_mode_nuclear_test;
mod ccs_comprehensive_spam_verification;
mod ccs_delta_spam_systematic_reproduction;
mod ccs_extreme_streaming_regression;
mod ccs_nuclear_full_log_regression;
mod ccs_nuclear_spam_test;
mod ccs_real_world_log_regression;
mod ccs_streaming_edge_cases;
mod ccs_streaming_spam_all_deltas;
mod ccs_wrapping_comprehensive;
mod ccs_wrapping_waterfall_reproduction;
mod cli;
mod cloud;
mod codex_duplicate_item_completed;
mod codex_reasoning_spam_regression;
mod commit;
mod commit_xml_validation;
mod common;
mod deduplication;
mod development_xml_validation;
mod event_loop_trace_dump;
mod fix_xml_validation;
mod gemini_parser_tests;
mod git;
mod gitignore_enforcement;
mod lifecycle_event_removal_regression;
mod logger;
mod logging_per_run;
mod loop_detection_after_additional_events;
mod loop_detection_recovery;
mod memory_safety;
mod opencode_parser_tests;
mod opencode_usage_limit_detection;
mod plan_xml_validation;
mod prompt_path_resolution;
mod prompt_permissions;
mod reducer_agent_fallback;
mod reducer_effect_invariants;
mod reducer_error_handling;
mod reducer_fault_tolerance;
mod reducer_hidden_behavior;
mod reducer_legacy_rejection;
mod reducer_rebase_state_machine;
mod reducer_resume;
mod reducer_resume_boundary_tests;
mod reducer_resume_tests;
mod reducer_state_machine;
mod required_files_cleanup;
mod review_output_validation;
mod review_xml_validation;
mod rust_lsp_dylint;
mod template_rendering_errors;
mod template_validation_jsx;
mod test_count_guard;
mod test_timeout;
mod test_traits;
mod timeout_file_activity;
mod ui_events;
mod workflows;
mod xsd_retry_missing_files;
mod xsd_retry_workflow;

mod dylint_target;
