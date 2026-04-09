//! Integration tests for ralph-workflow — Agent and core tests
//!
//! This binary contains agent, parser, and core infrastructure tests:
//! - CCS streaming and parsing
//! - Agent chain and spawn behavior
//! - Memory safety tests
//! - Git and cloud operations
//! - Parser tests (gemini, opencode, codex)

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

// Agent behavior
#[path = "../integration_tests/agent_chain_normalization.rs"]
mod agent_chain_normalization;
#[path = "../integration_tests/agent_spawn_errors.rs"]
mod agent_spawn_errors;

// CCS streaming tests
#[path = "../integration_tests/ccs_all_delta_types_spam_reproduction.rs"]
mod ccs_all_delta_types_spam_reproduction;
#[path = "../integration_tests/ccs_ansi_stripping_console.rs"]
mod ccs_ansi_stripping_console;
#[path = "../integration_tests/ccs_ansi_stripping_waterfall.rs"]
mod ccs_ansi_stripping_waterfall;
#[path = "../integration_tests/ccs_basic_mode_nuclear_test.rs"]
mod ccs_basic_mode_nuclear_test;
#[path = "../integration_tests/ccs_comprehensive_spam_verification.rs"]
mod ccs_comprehensive_spam_verification;
#[path = "../integration_tests/ccs_delta_spam_systematic_reproduction.rs"]
mod ccs_delta_spam_systematic_reproduction;
#[path = "../integration_tests/ccs_extreme_streaming_regression.rs"]
mod ccs_extreme_streaming_regression;
#[path = "../integration_tests/ccs_nuclear_full_log_regression.rs"]
mod ccs_nuclear_full_log_regression;
#[path = "../integration_tests/ccs_nuclear_spam_test.rs"]
mod ccs_nuclear_spam_test;
#[path = "../integration_tests/ccs_real_world_log_regression.rs"]
mod ccs_real_world_log_regression;
#[path = "../integration_tests/ccs_streaming_edge_cases.rs"]
mod ccs_streaming_edge_cases;
#[path = "../integration_tests/ccs_streaming_spam_all_deltas.rs"]
mod ccs_streaming_spam_all_deltas;
#[path = "../integration_tests/ccs_wrapping_comprehensive.rs"]
mod ccs_wrapping_comprehensive;
#[path = "../integration_tests/ccs_wrapping_waterfall_reproduction.rs"]
mod ccs_wrapping_waterfall_reproduction;

// Codex parser tests
#[path = "../integration_tests/codex_duplicate_item_completed.rs"]
mod codex_duplicate_item_completed;
#[path = "../integration_tests/codex_reasoning_spam_regression.rs"]
mod codex_reasoning_spam_regression;

// Gemini parser tests
#[path = "../integration_tests/gemini_parser_tests.rs"]
mod gemini_parser_tests;

// OpenCode parser tests
#[path = "../integration_tests/opencode_parser_tests.rs"]
mod opencode_parser_tests;
#[path = "../integration_tests/opencode_usage_limit_detection.rs"]
mod opencode_usage_limit_detection;

// Deduplication
#[path = "../integration_tests/deduplication/mod.rs"]
mod deduplication;

// Git operations
#[path = "../integration_tests/git/mod.rs"]
mod git;

// Cloud operations
#[path = "../integration_tests/cloud/mod.rs"]
mod cloud;

// Offline detection
#[path = "../integration_tests/offline_detection.rs"]
mod offline_detection;

// Prompt operations
#[path = "../integration_tests/prompt_path_resolution.rs"]
mod prompt_path_resolution;
#[path = "../integration_tests/prompt_permissions.rs"]
mod prompt_permissions;

// Makefile install test
#[path = "../integration_tests/makefile_install.rs"]
mod makefile_install;
