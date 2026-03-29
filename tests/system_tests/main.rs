// Lint policy: git-backed system tests remain governed by the same style guide, with
// only the smallest libgit2/system-boundary exceptions kept local to this binary.
//
// See `CODE_STYLE.md`, `docs/code-style/testing.md`, `docs/code-style/boundaries.md`,
// and `tests/clippy.toml`.
#![deny(warnings)]
#![deny(clippy::all)]
// Note: unsafe_code is allowed because system tests need to send signals to processes (kill, SIGINT, etc.)
#![deny(
    // No explicit iterator loops when a more idiomatic form exists
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    // These tests touch real git/system boundaries, but should still reject debug
    // leftovers and prefer the shared functional/test baseline where applicable.
    clippy::dbg_macro,
    // Push toward combinators instead of hand-written control flow
    clippy::manual_map,
    clippy::manual_filter,
    clippy::manual_find,
    clippy::manual_filter_map,
    clippy::manual_flatten,
    clippy::needless_collect
)]
//! system_tests: real git repository operations.
//!
//! ALL tests in this binary use `git2::Repository` or `init_git_repo()`, which
//! wraps libgit2 — a C library with a global reference counter. Concurrent drops
//! of `Repository` objects across threads cause SIGABRT. For this reason ALL
//! tests in this binary carry `#[serial]`.
//!
//! This is NOT a design smell in Ralph's code; it is an inherent libgit2
//! constraint. Tests that do not touch libgit2 belong in `process-system-tests`.
//!
//! # WARNING: DO NOT ADD NEW SYSTEM TESTS WITHOUT APPROVAL
//!
//! System tests are a **LAST RESORT**. Before adding ANY new test here:
//! 1. Write an RFC explaining why `MemoryWorkspace` + mocks won't work
//! 2. Get explicit user approval
//! 3. Verify you're testing a BOUNDARY function, not application logic
//!
//! If testing CLI/pipeline behavior, use integration tests with proper mocking.
//!
//! NOTE: The `signal_cleanup` module validates Ctrl+C/SIGINT cleanup semantics
//! end-to-end (real signal delivery, file permission bits, hook restoration). It
//! uses `init_git_repo()` for setup and therefore remains in this serial binary.
//!
//! These tests are **NOT** part of CI. Run manually as sanity checks.
//! See `SYSTEM_TESTS.md` for full guidelines and `docs/agents/testing-guide.md`
//! for the canonical testing strategy.
//!
//! # Running System Tests
//!
//! ```bash
//! cargo test -p ralph-workflow-tests --test system_tests
//! ```
//!
//! # When to Use System Tests
//!
//! ONLY for testing **boundary implementations**:
//! - `WorkspaceFs` (the real filesystem `Workspace` impl)
//! - Direct `git2` wrapper functions
//! - File permission/symlink edge cases
//!
//! # NOT For
//!
//! - CLI behavior (use integration tests)
//! - Pipeline logic (use integration tests)
//! - Anything testable with `MemoryWorkspace` + `MockProcessExecutor`
//! - Tests that do not use libgit2 (use `process-system-tests` instead)

mod common;
mod test_timeout;

// Test modules using real git2/libgit2 operations (require #[serial])
// NOTE: agents and deduplication moved to process-system-tests binary (no libgit2 needed)
mod file_protection;
mod git;
mod prompt_permissions;
mod rebase;
mod rebase_checkpoint;
mod rebase_state_machine;
mod signal_cleanup;
