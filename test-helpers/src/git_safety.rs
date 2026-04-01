//! Hard safety guardrails preventing tests from touching real git state.
//!
//! # Policy
//!
//! No test is permitted to mutate real git state (commits, branches, tags,
//! index writes, resets). This module provides helpers that panic immediately
//! with a clear policy message if real git mutation is attempted.
//!
//! This is enforced unconditionally — there is no environment variable or
//! feature flag to bypass this requirement.

/// Panics immediately with a clear policy violation message.
///
/// Call this at the entry point of any function that would perform real git
/// mutation if reached from a test context.
///
/// # Panics
///
/// Always panics with a policy violation message.
#[track_caller]
pub fn no_real_git_mutation(operation: &str) {
    panic!(
        "GIT MUTATION POLICY VIOLATION: test attempted real git operation '{}'. \
         Tests must use in-memory fakes (MockAppEffectHandler, MockWorkspace) \
         instead of real git state. See test-helpers/src/git_safety.rs for policy.",
        operation
    );
}
