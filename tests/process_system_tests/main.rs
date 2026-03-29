// Lint policy: process system tests are still style-guide-governed boundary tests.
//
// See `CODE_STYLE.md`, `docs/code-style/testing.md`, `docs/code-style/boundaries.md`,
// and `tests/clippy.toml`.
#![deny(warnings)]
#![deny(clippy::all)]
// Note: unsafe_code is allowed because process system tests need to set process groups
#![deny(
    // No explicit iterator loops when a more idiomatic form exists
    clippy::explicit_iter_loop,
    clippy::explicit_into_iter_loop,
    // This binary needs real process/system interaction, but it should still avoid
    // accidental debug leftovers and imperative-style anti-patterns where possible.
    clippy::dbg_macro,
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
//! `system_tests` binary so that the libgit2 global
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
