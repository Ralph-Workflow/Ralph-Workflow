//! Runtime primitives for git_helpers boundary module.
//!
//! This module is a boundary seam for signal-handler registration and
//! the test serialisation lock. Process-global phase-state lives in
//! `phase_state` to keep it importable from non-boundary modules.

pub use crate::git_helpers::hooks;

#[cfg(any(test, feature = "test-utils"))]
#[must_use]
pub fn agent_phase_test_lock() -> &'static std::sync::Mutex<()> {
    static TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    &TEST_LOCK
}
