//! Integration tests for ralph-workflow — Reducer tests
//!
//! This binary contains reducer-heavy integration tests:
//! - State machine, error handling, resume, fault tolerance
//! - Loop detection and recovery
//! - Legacy rejection invariants

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

// Reducer core
#[path = "../integration_tests/reducer_error_handling.rs"]
mod reducer_error_handling;
#[path = "../integration_tests/reducer_fault_tolerance/mod.rs"]
mod reducer_fault_tolerance;
#[path = "../integration_tests/reducer_hidden_behavior.rs"]
mod reducer_hidden_behavior;
#[path = "../integration_tests/reducer_legacy_rejection/mod.rs"]
mod reducer_legacy_rejection;
#[path = "../integration_tests/reducer_rebase_state_machine.rs"]
mod reducer_rebase_state_machine;
#[path = "../integration_tests/reducer_resume.rs"]
mod reducer_resume;
#[path = "../integration_tests/reducer_resume_boundary_tests.rs"]
mod reducer_resume_boundary_tests;
#[path = "../integration_tests/reducer_resume_tests.rs"]
mod reducer_resume_tests;
#[path = "../integration_tests/reducer_state_machine.rs"]
mod reducer_state_machine;

// Reducer coordination
#[path = "../integration_tests/reducer_agent_fallback.rs"]
mod reducer_agent_fallback;
#[path = "../integration_tests/reducer_effect_invariants.rs"]
mod reducer_effect_invariants;

// Recovery and fault tolerance
#[path = "../integration_tests/awaiting_dev_fix_recovery/mod.rs"]
mod awaiting_dev_fix_recovery;
#[path = "../integration_tests/loop_detection_after_additional_events.rs"]
mod loop_detection_after_additional_events;
#[path = "../integration_tests/loop_detection_recovery.rs"]
mod loop_detection_recovery;
