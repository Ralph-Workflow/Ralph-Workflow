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

/// Fail-fast guardrail: panic immediately with a policy error if `path` is
/// inside any real git repository (checked by walking ancestors for .git).
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if a `.git` directory is found in any
/// ancestor of `path`.
#[track_caller]
pub fn assert_not_real_git_repo(path: &std::path::Path) {
    crate::boundary::assert_not_real_git_repo_impl(path);
}

/// Fail-fast guardrail: panic if a workspace root value is `None`.
///
/// This catches the class of bugs where `RealAppEffectHandler` (or any handler)
/// is constructed without a workspace root, which later causes "workspace root
/// is not set" errors deep in git or file operations.
///
/// Call this at test entry points that create effect handlers to verify the
/// workspace root is properly initialized before any operations are attempted.
///
/// # Panics
///
/// Panics with a clear policy message if `workspace_root` is `None`.
#[track_caller]
pub fn assert_effect_handler_has_workspace_root(workspace_root: Option<&std::path::Path>) {
    if workspace_root.is_none() {
        panic!(
            "WORKSPACE ROOT POLICY VIOLATION: effect handler was constructed without a \
             workspace root. All effect handlers used in tests must have a workspace root \
             set — either via with_workspace_root(path) or via new() (which defaults to \
             cwd). A missing workspace root causes 'workspace root is not set' errors \
             in git and file operations. See test-helpers/src/git_safety.rs for policy.",
        );
    }
}

/// Fail-fast guardrail: panic immediately with a policy error if `path` is
/// inside the project git repository.
///
/// Unlike `assert_not_real_git_repo` which panics if `path` is inside ANY git
/// repository, this function only panics if `path` is inside THE PROJECT
/// repository specifically.
///
/// # Panics
///
/// Panics with a POLICY VIOLATION message if the project git directory is found
/// in the ancestor chain of `path`.
#[track_caller]
pub fn assert_in_isolated_temp_repo(path: &std::path::Path) {
    crate::boundary::assert_in_isolated_temp_repo_impl(path);
}
