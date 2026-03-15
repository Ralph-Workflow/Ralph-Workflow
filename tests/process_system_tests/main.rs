// DO NOT CHANGE LINTING POLICY UNLESS THE USER SPECIFICALLY ASKS TO, YOU MUST REFACTOR EVEN IF IT TAKES YOU LONG TIME
//
// Note: clippy::cargo is not enabled because it flags transitive dependency version conflicts
// (e.g., bitflags 1.3.2 from inotify vs 2.10.0 from other crates) which are ecosystem-level
// issues outside our control and don't reflect code quality problems.
#![deny(warnings)]
#![deny(clippy::all)]
#![forbid(unsafe_code)]
#![deny(
    // No explicit iterator loops when a more idiomatic form exists
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    // No implicit crashes / partial operations
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::panic_in_result_fn,
    clippy::indexing_slicing,
    // No casual side effects / debugging leftovers
    clippy::print_stdout,
    clippy::print_stderr,
    clippy::dbg_macro,
    // Treat unchecked arithmetic as suspicious
    clippy::arithmetic_side_effects,
    // Push toward combinators instead of hand-written control flow
    clippy::manual_map,
    clippy::manual_filter,
    clippy::manual_find,
    clippy::manual_filter_map,
    clippy::manual_flatten,
    clippy::needless_collect
)]
//! Process-level system tests: agent binary discovery.
//!
//! These tests require real OS processes, real file permissions, or real PATH
//! discovery, but do NOT use libgit2. They are intentionally separated from the
//! `git2-system-tests` binary so that the libgit2 global
//! reference-counter constraint does not force serialization here.
//!
//! # Parallelism
//!
//! Tests in this binary run in parallel by default (standard Rust behavior).
//! Each test must create its own isolated `TempDir` and spawned processes.
//! If a test modifies process-global state (e.g. PATH), it must hold the
//! module-local `ENV_LOCK` Mutex for the duration; it must NOT use `#[serial]`.
//!
//! # Not in CI
//!
//! System tests are run manually only. See `docs/agents/testing-guide.md`.

mod agents;
mod deduplication;
