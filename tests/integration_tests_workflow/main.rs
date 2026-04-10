//! Integration tests for ralph-workflow — Workflow and process tests
//!
//! This binary contains workflow and process integration tests:
//! - End-to-end workflow tests
//! - CLI and commit behavior
//! - XML validation
//! - Logging and tracing

#![deny(warnings)]
#![deny(clippy::all)]
#![forbid(unsafe_code)]
#![deny(
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    clippy::dbg_macro,
    clippy::manual_map,
    clippy::manual_filter,
    clippy::manual_find,
    clippy::manual_filter_map,
    clippy::manual_flatten,
    clippy::needless_collect
)]

// Shared test utilities (compiled into each test binary separately).
// NOTE: test_count_guard is NOT included here — it uses include_str! paths
// relative to integration_tests/ which would break when compiled from a different root.
// NOTE: Each binary only includes a subset of test modules, so common helper functions
// that are only used by excluded modules appear as dead code in each binary.
// The dead code is real at the binary level but not at the full project level.
#[path = "../integration_tests/common/mod.rs"]
#[expect(
    dead_code,
    reason = "common module has functions used only by test modules in other split binaries; dead at binary level but used project-wide"
)]
mod common;
#[path = "../integration_tests/test_timeout.rs"]
mod test_timeout;
#[path = "../integration_tests/test_traits.rs"]
mod test_traits;

// Workflow tests
#[path = "../integration_tests/behavioral_pipeline_tests.rs"]
mod behavioral_pipeline_tests;
#[path = "../integration_tests/workflows/mod.rs"]
mod workflows;

// CLI
#[path = "../integration_tests/cli/mod.rs"]
mod cli;

// Commit behavior
#[path = "../integration_tests/commit/mod.rs"]
mod commit;
#[path = "../integration_tests/commit_xml_validation.rs"]
mod commit_xml_validation;

// XML validation
#[path = "../integration_tests/development_xml_validation.rs"]
mod development_xml_validation;
#[path = "../integration_tests/fix_xml_validation.rs"]
mod fix_xml_validation;
#[path = "../integration_tests/plan_xml_validation.rs"]
mod plan_xml_validation;
#[path = "../integration_tests/review_output_validation.rs"]
mod review_output_validation;
#[path = "../integration_tests/review_xml_validation.rs"]
mod review_xml_validation;

// XSD retry
#[path = "../integration_tests/xsd_retry_missing_files.rs"]
mod xsd_retry_missing_files;
#[path = "../integration_tests/xsd_retry_workflow.rs"]
mod xsd_retry_workflow;

// Logging and tracing
#[path = "../integration_tests/event_loop_trace_dump.rs"]
mod event_loop_trace_dump;
#[path = "../integration_tests/logger/mod.rs"]
mod logger;
#[path = "../integration_tests/logging_per_run.rs"]
mod logging_per_run;

// Memory safety (depends on workflows module)
#[path = "../integration_tests/memory_safety/mod.rs"]
mod memory_safety;

// Test infrastructure and utilities
#[path = "../integration_tests/timeout_file_activity.rs"]
mod timeout_file_activity;
#[path = "../integration_tests/ui_events.rs"]
mod ui_events;

// Filesystem and gitignore
#[path = "../integration_tests/gitignore_enforcement.rs"]
mod gitignore_enforcement;
#[path = "../integration_tests/required_files_cleanup.rs"]
mod required_files_cleanup;

// Template
#[path = "../integration_tests/template_rendering_errors.rs"]
mod template_rendering_errors;
#[path = "../integration_tests/template_validation_jsx.rs"]
mod template_validation_jsx;

// MCP server behavioral tests
#[path = "../integration_tests/mcp_artifact_reducer_parity.rs"]
mod mcp_artifact_reducer_parity;
#[path = "../integration_tests/mcp_behavioral.rs"]
mod mcp_behavioral;

// Dylint makefile
#[path = "../integration_tests/dylint_target.rs"]
mod dylint_target;
#[path = "../integration_tests/rust_lsp_dylint.rs"]
mod rust_lsp_dylint;
