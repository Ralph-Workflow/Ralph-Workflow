//! Tests verifying legacy fallback paths are removed.
//!
//! These tests assert that the pipeline does NOT fall back to legacy
//! artifact locations. They verify that reducer state is the single source
//! of truth and legacy file-based fallbacks have been eliminated.
//!
//! # Integration Test Style Guide
//!
//! **CRITICAL:** All tests in this module MUST follow integration test style guide
//! defined in **[../../INTEGRATION_TESTS.md](../../INTEGRATION_TESTS.md)**.
//!
//! Key principles applied in this module:
//! - Tests verify **observable behavior** (rejection of legacy paths)
//! - Tests are deterministic and isolated
//! - Tests use `MemoryWorkspace` for filesystem isolation

mod archival_invariants;
mod checkpoint_format;
mod legacy_phase_rejection;
mod reducer_purity_invariants;
