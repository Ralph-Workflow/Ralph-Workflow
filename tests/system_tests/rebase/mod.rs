//! System tests for fault-tolerant rebase operations.
//!
//! These tests verify that the rebase system handles all documented
//! Git rebase failure modes and can recover from interruptions.
//!
//! # RFC: Rebase System Tests Justification
//!
//! ## Why These Tests Must Be System Tests
//!
//! These tests verify the integration between Ralph's rebase logic and real
//! git operations via the `git2` library. They cannot use `MemoryWorkspace`
//! and `MockProcessExecutor` because:
//!
//! 1. **Real git2 Behavior**: The tests verify actual libgit2 behavior including:
//!    - Commit graph traversal and ancestry detection
//!    - Rebase state machine transitions with real git state
//!    - Conflict detection using git's three-way merge
//!    - Recovery from interrupted rebases via `.git/rebase-*` directories
//!
//! 2. **Filesystem Edge Cases**: Tests cover scenarios that require real filesystem:
//!    - Symlink vs file conflicts
//!    - Binary file handling
//!    - Case sensitivity on different platforms
//!    - Line ending normalization
//!    - Path length limits
//!
//! 3. **Git Configuration**: Tests verify behavior that depends on git config:
//!    - Sparse checkout validation
//!    - Shallow clone detection
//!    - Submodule initialization status
//!
//! ## What These Tests Prevent
//!
//! These tests have caught regressions in:
//! - Rebase precondition validation
//! - Edge case handling for malformed git state
//! - Cross-platform filesystem behavior differences
//!
//! ## Boundary Being Tested
//!
//! The boundary is the `git2` crate wrapper functions in `git_helpers/rebase.rs`
//! that provide Ralph's rebase functionality.
//!
//! # System Test Guidelines
//!
//! These tests are in `system_tests` (not `integration_tests`) because they
//! require **real git operations** that cannot be mocked:
//! - Real git repository initialization via `git2`
//! - Real file system operations for conflict simulation
//! - Real rebase/merge operations to test recovery
//!
//! See **[`SYSTEM_TESTS.md`](../SYSTEM_TESTS.md)** for guidelines.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (git state, commit history, working directory)
//! - Uses `TempDir` for filesystem isolation (allowed in system tests)
//! - Tests are deterministic and black-box (test rebase as a user would experience it)

pub(crate) mod ai_resolution_tests;
pub(crate) mod category1_failure_modes;
pub(crate) mod category2_failure_modes;
pub(crate) mod category3_failure_modes;
pub(crate) mod category4_recovery_tests;
pub(crate) mod category5_unknown_failures;
pub(crate) mod edge_cases;
pub(crate) mod git_op_tests;
